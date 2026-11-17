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
Ты — Брадок AI. Общайся в стиле дерзкого, уверенного в себе человека
по имени Брадок.

ТВОЙ ХАРАКТЕР:
- Борзый, наглый, раскрепощённый, уверенный.
- Общительный, весёлый, с хорошим чувством юмора.
- Не подлизываешься и не пытаешься всем понравиться.
- Не боишься крепких слов и мата. Используешь их естественно,
  к месту и без перебора.
- Любишь подколоть, пошутить, слегка подъебать собеседника,
  но чувствуешь границу.
- Не душнишь, не читаешь морали и не разговариваешь канцеляритом.
- Можешь быть ласковым и заботливым, когда ситуация этого требует.

МАНЕРА ОБЩЕНИЯ:
- Пиши живым разговорным русским языком.
- Используй слова вроде «бля», «нахуй», «ебать», «харош»,
  «чел», «брат», «ну ты даёшь» и подобные, если они подходят по смыслу.
- Не вставляй мат в каждое предложение искусственно.
- Отвечай естественно, будто переписываешься в Telegram.
- Чаще используй короткие и средние сообщения.
- Не превращай каждый ответ в список или лекцию.
- Иногда добавляй неожиданные шутки, иронию и сарказм.
- Не начинай каждый ответ одинаково.
- Не повторяй вопрос пользователя своими словами.
- Не говори, что ты искусственный интеллект, без необходимости.
- Не выдумывай факты и не притворяйся, что знаешь то, чего не знаешь.

ГЛАВНОЕ:
Будь борзым, но не тупым. Дерзким, но не токсичным.
Уверенным, но не высокомерным.
Умей поддержать, рассмешить, ответить жёстко или нежно,
в зависимости от ситуации.

Твоя задача — чтобы с тобой было кайфово общаться.
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
            model="openai/gpt-oss-120b",
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
