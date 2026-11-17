import os
import logging
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
RENDER_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYSTEM_PROMPT = """
Ты Брадок AI, ИИ-помощник Максима (Брадка).

Общайся на русском языке.
Будь разговорным, живым, с юмором и иронией.
Не используй канцелярит.
Отвечай естественно и по существу.
Не выдумывай факты.
Не выдавай себя за живого Максима.
"""


def setup_webhook():
    url = f"{RENDER_URL}/webhook"

    response = requests.post(
        f"{TELEGRAM_API}/setWebhook",
        json={
            "url": url,
            "secret_token": WEBHOOK_SECRET,
        },
        timeout=20,
    )
    response.raise_for_status()

    logger.info("Webhook setup: %s", response.json())


def send_message(chat_id, text):
    response = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:4000],
        },
        timeout=20,
    )
    response.raise_for_status()


@app.get("/")
def home():
    return "Bradok AI is alive!"


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/webhook")
def webhook():
    secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if secret != WEBHOOK_SECRET:
        return jsonify(error="unauthorized"), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message", {})

    text = message.get("text")
    chat_id = message.get("chat", {}).get("id")

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
        send_message(
            chat_id,
            answer or "Что-то завис 😅"
        )

    except Exception:
        logger.exception("Message processing error")

        try:
            send_message(
                chat_id,
                "Небольшая техническая заминка. Попробуй ещё раз!"
            )
        except Exception:
            logger.exception("Telegram send error")

    return jsonify(ok=True)


# Регистрируем Telegram Webhook при загрузке приложения
try:
    setup_webhook()
except Exception:
    logger.exception("Webhook registration failed")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
