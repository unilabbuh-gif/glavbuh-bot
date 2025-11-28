import os
import logging
import requests
from flask import Flask, request

# Логирование, чтобы в Render в логах всё было видно
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

app = Flask(__name__)


def send_message(chat_id: int, text: str):
    """Отправка сообщения обратно в Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            logger.error("Telegram send_message error: %s", r.text)
    except Exception as e:
        logger.exception("Exception while sending message to Telegram: %s", e)


def ask_openai(user_text: str) -> str:
    """Запрос к OpenAI как к главбуху, с обработкой ошибок."""
    url = "https://api.openai.com/v1/chat/completions"

    system_prompt = """
Ты — виртуальный главный бухгалтер Николая.
Отвечай чётко, структурно.
Если пользователь просит платёжку — выдавай JSON:
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
Если данных не хватает — добавь вопросы в need_clarification.
Отвечай всегда на русском языке.
"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        # ЭТО ВАЖНО: нормальная доступная модель
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=60)
    except Exception as e:
        logger.exception("Error while calling OpenAI: %s", e)
        return "Не удалось обратиться к модели (ошибка сети). Попробуй ещё раз позже."

    # Если OpenAI вернул не 200 — пишем в лог и отвечаем текстом
    if resp.status_code != 200:
        logger.error("OpenAI HTTP error %s: %s", resp.status_code, resp.text)
        return f"OpenAI вернул ошибку {resp.status_code}. Подробности смотри в логах Render."

    try:
        response_json = resp.json()
    except Exception as e:
        logger.exception("Cannot decode OpenAI JSON: %s. Raw: %s", e, resp.text)
        return "Не удалось разобрать ответ от модели OpenAI."

    # Если в ответе есть 'error' — логируем и возвращаем текст
    if "error" in response_json:
        logger.error("OpenAI API error: %s", response_json["error"])
        return f"OpenAI сообщил об ошибке: {response_json['error']}"

    # Нормальный случай: есть choices
    try:
        return response_json["choices"][0]["message"]["content"]
    except Exception as e:
        logger.exception("OpenAI response format unexpected: %s. JSON: %s", e, response_json)
        return "Модель ответила в неожиданном формате. Смотри логи Render для деталей."


@app.route("/", methods=["POST"])
def telegram_webhook():
    """Основной обработчик вебхука Telegram."""
    try:
        message = request.json
        logger.info("Incoming update: %s", message)

        if not message or "message" not in message:
            return "ok"

        msg = message["message"]
        chat_id = msg["chat"]["id"]

        # Если это не текст (стикер, фото и т.п.) — просто игнорируем
        if "text" not in msg:
            send_message(chat_id, "Я пока понимаю только текстовые сообщения.")
            return "ok"

        text = msg["text"]

        # Запрашиваем OpenAI
        reply = ask_openai(text)

        # Отвечаем пользователю
        send_message(chat_id, reply)

    except Exception as e:
        logger.exception("Exception in telegram_webhook: %s", e)

    return "ok"


if __name__ == "__main__":
    app.run(port=5000)
