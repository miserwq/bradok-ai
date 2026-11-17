
import os
import logging
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
RENDER_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты Брадок AI, ИИ-помощник Максима (Брадка).

Стиль общения:
- Разговорный русский язык.
- Юмор, ирония и живые эмоции.
- Естественные фразы без канцелярита.
- Уместные шутки и короткие ответы.
- Не выдумывай факты.
- Если чего-то не знаешь, скажи честно.
- Не выдавай себя за живого Максима.
"""

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def telegram_send(chat_id, text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text[:4000]},
        timeout=20,
    ).raise_for_status()


def setup_webhook():
    response = requests.post(
        f"{TELEGRAM_API}/setWebhook",
        json={
            "url": f"{RENDER_URL}/webhook",
            "secret_token": WEBHOOK_SECRET,
        },
        timeout=20,
    )
    response.raise_for_status()
    logger.info("Webhook configured: %s", response.json())


@app.get("/")
def home():
    return "Bradok AI is alive!"


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/webhook")
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return jsonify(error="unauthorized"), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message", {})
    text = message.get("text")
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not text or not chat_id:
        return jsonify(ok=True)

    try:
        result = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        answer = result.choices[0].message.content
        telegram_send(chat_id, answer or "Что-то завис 😅")

    except Exception:
        logger.exception("Message processing failed")
        try:
            telegram_send(
                chat_id,
                "У меня небольшая техническая заминка. Попробуй ещё раз!"
            )
        except Exception:
            logger.exception("Could not send error message")

    return jsonify(ok=True)


if __name__ == "__main__":
    setup_webhook()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


if __name__ != "__main__":
    # Gunicorn imports this module when starting the service.
    try:
        setup_webhook()
    except Exception:
        logger.exception("Webhook setup failed")
