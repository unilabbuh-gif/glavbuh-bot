import os
import requests
from flask import Flask, request

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

app = Flask(__name__)

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    requests.post(url, json=payload)

def ask_openai(user_text):
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
"""

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

    data = {
        "model": "gpt-4o-mini-tts",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",  "content": user_text}
        ]
    }

    response = requests.post(url, headers=headers, json=data).json()
    return response["choices"][0]["message"]["content"]

@app.route("/", methods=["POST"])
def telegram_webhook():
    message = request.json

    if "message" in message:
        chat_id = message["message"]["chat"]["id"]
        text = message["message"]["text"]

        reply = ask_openai(text)
        send_message(chat_id, reply)

    return "ok"

if __name__ == "__main__":
    app.run(port=5000)
