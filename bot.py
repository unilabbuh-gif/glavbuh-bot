import os
import logging
import requests
from flask import Flask, request

# ----------------------------
# БАЗОВЫЕ НАСТРОЙКИ
# ----------------------------

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

app = Flask(__name__)

# Логи, чтобы всё видно было в Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Память по пользователям: история сообщений, факты, задачи
chat_histories = {}  # {chat_id: [ {"role": "...", "content": "..."} ]}
memories = {}        # {chat_id: [ "факт 1", "факт 2", ... ]}
tasks = {}           # {chat_id: [ {id, text, status} ]}
next_task_id = 1     # простой счётчик задач


# ----------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ----------------------------

def send_message(chat_id: int, text: str):
    """Отправка сообщения пользователю в Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            logger.error("Telegram send_message error %s: %s", r.status_code, r.text)
    except Exception as e:
        logger.exception("Exception while sending message to Telegram: %s", e)


def call_openai(messages):
    """Универсальный вызов OpenAI Chat API с обработкой ошибок."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "gpt-4.1-mini",  # достаточно мощная и дешевая модель
        "messages": messages,
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=60)
    except Exception as e:
        logger.exception("Error while calling OpenAI: %s", e)
        return "Не удалось обратиться к модели (ошибка сети). Попробуй ещё раз позже."

    if resp.status_code != 200:
        logger.error("OpenAI HTTP error %s: %s", resp.status_code, resp.text)
        return f"OpenAI вернул ошибку {resp.status_code}. Подробности смотри в логах Render."

    try:
        response_json = resp.json()
    except Exception as e:
        logger.exception("Cannot decode OpenAI JSON: %s. Raw: %s", e, resp.text)
        return "Не удалось разобрать ответ от модели OpenAI."

    if "error" in response_json:
        logger.error("OpenAI API error: %s", response_json["error"])
        return f"OpenAI сообщил об ошибке: {response_json['error']}"

    try:
        return response_json["choices"][0]["message"]["content"]
    except Exception as e:
        logger.exception(
            "OpenAI response format unexpected: %s. JSON: %s",
            e,
            response_json,
        )
        return "Модель ответила в неожиданном формате. Смотри логи Render для деталей."


# ----------------------------
# ПАМЯТЬ И ЗАДАЧИ
# ----------------------------

def add_memory(chat_id: int, fact: str):
    """Запомнить факт про бизнес."""
    fact = fact.strip()
    if not fact:
        return
    mem = memories.get(chat_id, [])
    mem.append(fact)
    memories[chat_id] = mem[-50:]  # ограничим 50 фактами


def get_memory_block(chat_id: int) -> str:
    """Вернуть текстовый блок с фактами для подмешивания в промпт."""
    mem = memories.get(chat_id, [])
    if not mem:
        return ""
    text = "Факты о бизнесе Николая, которые нужно учитывать:\n"
    for i, f in enumerate(mem, start=1):
        text += f"{i}. {f}\n"
    return text


def add_task(chat_id: int, text: str):
    """Создать новую задачу для пользователя."""
    global next_task_id
    t = text.strip()
    if not t:
        return None
    task_list = tasks.get(chat_id, [])
    task = {
        "id": next_task_id,
        "text": t,
        "status": "open",
    }
    next_task_id += 1
    task_list.append(task)
    tasks[chat_id] = task_list
    return task


def list_tasks(chat_id: int) -> str:
    """Список задач для пользователя."""
    task_list = tasks.get(chat_id, [])
    if not task_list:
        return "У тебя пока нет активных задач."
    lines = []
    for t in task_list:
        status = "✅" if t["status"] == "done" else "🔸"
        lines.append(f"{status} #{t['id']}: {t['text']}")
    return "Твои задачи:\n" + "\n".join(lines)


def complete_task(chat_id: int, task_id: int) -> str:
    """Отметить задачу выполненной."""
    task_list = tasks.get(chat_id, [])
    for t in task_list:
        if t["id"] == task_id:
            t["status"] = "done"
            return f"Задача #{task_id} помечена как выполненная ✅"
    return f"Задача #{task_id} не найдена."


# ----------------------------
# РЕЖИМЫ РАБОТЫ БОТА
# ----------------------------

def ask_openai_chat(chat_id: int, user_text: str) -> str:
    """Режим обычного главбуха-консультанта с контекстом и памятью."""
    base_system_prompt = """
Ты — виртуальный главный бухгалтер и финансовый директор.
Твоя задача — помогать Николаю как опытный главбух:
- объяснять налоги, проводки, УСН/ОСНО, НДС, страховые взносы;
- предлагать, как безопасно и законно оформить операции;
- отвечать коротко, структурно, по делу, без воды;
- писать по-русски, понятным деловым языком;
- если данных мало — задавать уточняющие вопросы.

Всегда исходи из российского законодательства (НК РФ, ТК РФ и т.п.).
"""

    memory_block = get_memory_block(chat_id)
    if memory_block:
        system_prompt = base_system_prompt + "\n\n" + memory_block
    else:
        system_prompt = base_system_prompt

    history = chat_histories.get(chat_id, [])

    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": user_text}
    ]

    reply = call_openai(messages)

    # Обновляем историю (храним последние 10 сообщений диалога)
    try:
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        chat_histories[chat_id] = history[-10:]
    except Exception as e:
        logger.exception("Error updating chat history: %s", e)

    return reply


def ask_openai_payment(user_text: str) -> str:
    """Режим генерации платёжки с выдачей JSON."""
    system_prompt = """
Ты — виртуальный главный бухгалтер. 
Твоя задача: по текстовому описанию сформировать платежное поручение.

Формат ответа:
1) Кратко, 2–4 строки, поясни суть платежа человеческим языком.
2) Затем на новой строке напиши: JSON:
3) Далее выведи ТОЛЬКО один объект JSON без пояснений, строго по шаблону:

{
 "type": "payment",
 "payer_name": "...",
 "payer_inn": "...",
 "payer_kpp": "...",
 "payer_account": "...",
 "receiver_name": "...",
 "receiver_inn": "...",
 "receiver_kpp": "...",
 "receiver_account": "...",
 "bank_bik": "...",
 "amount_rub": 0,
 "amount_kop": 0,
 "is_budget": false,
 "kbk": null,
 "oktmo": null,
 "uin": null,
 "tax_period": null,
 "purpose": "...",
 "need_clarification": []
}

Правила:
- amount_rub и amount_kop — целые числа.
- is_budget = true, если это платеж в бюджет (налоги, взносы, штрафы и т.п.).
- Если это бюджетный платеж и не хватает данных (КБК, ОКТМО, УИН, период) — ставь null и добавляй вопросы в массив need_clarification.
- Если это обычный хозяйственный платеж (поставщик, аренда и т.п.) — is_budget = false, kbk/oktmo/uin/tax_period = null.
- В need_clarification пиши короткие вопросы по-русски.
- Если всё понятно и реквизиты полные — массив need_clarification оставь пустым.

Отвечай всегда на русском языке.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    reply = call_openai(messages)
    return reply


def is_payment_request(text: str) -> bool:
    """Похоже ли сообщение на запрос платёжки."""
    t = text.lower()
    keywords = [
        "платежку",
        "платёжку",
        "платежка",
        "платёжка",
        "платежное поручение",
        "платежным поручением",
        "сделай платеж",
        "сделай платёж",
        "оплата",
        "переведи",
        "перечислить",
    ]
    return any(k in t for k in keywords)


# ----------------------------
# ОБРАБОТЧИК TELEGRAM WEBHOOK
# ----------------------------

@app.route("/", methods=["POST"])
def telegram_webhook():
    """Главный вебхук: принимает апдейты от Telegram."""
    try:
        update = request.json
        logger.info("Incoming update: %s", update)

        if not update or "message" not in update:
            return "ok"

        msg = update["message"]
        chat_id = msg["chat"]["id"]

        # Если не текст (фото, стикер и т.д.)
        if "text" not in msg:
            send_message(chat_id, "Пока понимаю только текстовые сообщения 🙂")
            return "ok"

        text = msg["text"].strip()
        lower = text.lower()

        # -------- Команды --------

        if text.startswith("/start"):
            send_message(
                chat_id,
                "Привет, я виртуальный Главбух 🤖\n\n"
                "Могу:\n"
                "• отвечать на вопросы по учёту и налогам;\n"
                "• помогать с проводками и договорами;\n"
                "• по фразам типа «Сделай платёжку КВАД → Квартал 200000 без НДС» "
                "формировать JSON платежного поручения.\n\n"
                "Дополнительно:\n"
                "• «запомни: ...» — я запоминаю факт про твой бизнес;\n"
                "• «задача: ...» — создаю задачу и добавляю в список;\n"
                "• /tasks — показать все задачи;\n"
                "• /done 3 — пометить задачу #3 как выполненную;\n"
                "• /reset — очистить контекст диалога.\n",
            )
            return "ok"

        if text.startswith("/help"):
            send_message(
                chat_id,
                "Как со мной работать:\n\n"
                "💬 Обычные вопросы:\n"
                "  «Как провести аренду спецтехники в 1С?»\n"
                "  «Когда выгоднее УСН 6%, а когда 15%?»\n\n"
                "💸 Платёжка:\n"
                "  «Сделай платежку КВАД → Квартал 200000 без НДС по договору 5 от 20.10.2025»\n\n"
                "🧠 Память:\n"
                "  «запомни: ООО \"КВАД\" — наш подрядчик по самосвалам, без НДС»\n\n"
                "📋 Задачи:\n"
                "  «задача: проверить ЕНС по КВАД за октябрь»\n"
                "  /tasks — список задач\n"
                "  /done 1 — завершить задачу #1\n",
            )
            return "ok"

        if text.startswith("/reset"):
            chat_histories.pop(chat_id, None)
            send_message(chat_id, "Контекст диалога очищен. Начинаем с чистого листа 🙂")
            return "ok"

        if text.startswith("/tasks"):
            send_message(chat_id, list_tasks(chat_id))
            return "ok"

        if text.startswith("/done"):
            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                send_message(chat_id, "Напиши так: /done 3 — чтобы закрыть задачу #3.")
                return "ok"
            task_id = int(parts[1])
            send_message(chat_id, complete_task(chat_id, task_id))
            return "ok"

        # -------- Память --------

        if lower.startswith("запомни:"):
            fact = text.split(":", 1)[1]
            add_memory(chat_id, fact)
            send_message(chat_id, "Окей, запомнил 👍")
            return "ok"

        # -------- Задачи --------

        if lower.startswith("задача:"):
            task_text = text.split(":", 1)[1]
            task = add_task(chat_id, task_text)
            if task:
                send_message(
                    chat_id,
                    f"Задача #{task['id']} добавлена:\n{task['text']}",
                )
            else:
                send_message(chat_id, "Не смог создать задачу, текст пустой.")
            return "ok"

        # -------- Обычные сообщения / платёжки --------

        if is_payment_request(text):
            reply = ask_openai_payment(text)
        else:
            reply = ask_openai_chat(chat_id, text)

        send_message(chat_id, reply)

    except Exception as e:
        logger.exception("Exception in telegram_webhook: %s", e)

    return "ok"


if __name__ == "__main__":
    app.run(port=5000)
