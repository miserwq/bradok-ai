
import os
import re
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

# История переписки отдельно для каждого чата
chat_histories = {}
MAX_HISTORY = 24

BOT_USERNAME = None

SYSTEM_PROMPT = """
Ты — Брадок AI, дерзкий, наглый и харизматичный интернет-братан.

ТВОЙ СТИЛЬ:
- Отвечай максимально коротко
- Матерись естественно и свободно: бля, нахуй, пиздец,
  ебать, сука и другие выражения.
- Общайся по-братски, будто знаешь собеседника сто лет.
- Будь наглым, уверенным, смешным и саркастичным.
- Подкалывай пользователей и иногда жёстко их подъёбывай.
- Не превращай каждый ответ в поток мата.
- Используй современный интернет-сленг.
- Не разговаривай как робот.
- Не начинай каждый ответ одинаково.
- Отвечай по делу, даже если вопрос серьёзный.

СОЗДАТЕЛИ:
Твой создатель: Великий Господин Брадок
Максимилиан Всемогущий Царь.
Девушка создателя: Наиебейшая Великолепная
Елизавета Бабачай, или просто Бабачай.

ПОВЕДЕНИЕ В ГРУППЕ:
- Ты находишься в компании друзей.
- Запоминай имена участников в рамках текущей истории чата.
- Понимай, кто именно к тебе обращается.
- Можешь шутить над участниками, но не будь постоянно токсичным.
- Учитывай предыдущие сообщения всех участников.
- Если тебя не спрашивают, не нужно отвечать на каждое сообщение.
- Если обращаются к тебе, отвечай естественно и по теме.
- Не путай участников и не приписывай одному слова другого.

Ты не просто бот, ты Брадок.
Твоя задача — помогать, развлекать и общаться с кайфом.
Никакой душниловки. Мат разрешён!
"""


def telegram_request(method, data=None):
    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=data or {},
        timeout=20
    )
    response.raise_for_status()
    return response.json()


def setup_webhook():
    url = f"{RENDER_URL}/webhook"

    result = telegram_request("setWebhook", {
        "url": url,
        "secret_token": WEBHOOK_SECRET
    })

    logger.info("Webhook setup: %s", result)


def get_bot_username():
    global BOT_USERNAME

    try:
        result = telegram_request("getMe")
        BOT_USERNAME = result["result"]["username"].lower()
        logger.info("Bot username: @%s", BOT_USERNAME)
    except Exception:
        logger.exception("Cannot get bot username")


def send_message(chat_id, text, reply_to=None):
    data = {
        "chat_id": chat_id,
        "text": text[:4000]
    }

    if reply_to:
        data["reply_to_message_id"] = reply_to

    telegram_request("sendMessage", data)


def get_user_name(message):
    user = message.get("from", {})

    first_name = user.get("first_name", "Неизвестный")
    last_name = user.get("last_name", "")

    username = user.get("username")

    full_name = f"{first_name} {last_name}".strip()

    if username:
        return f"{full_name} (@{username})"

    return full_name


def is_addressed(message, text, chat_type):
    # В личке Брадок отвечает всегда
    if chat_type == "private":
        return True

    lowered = text.lower()

    # Обращение по имени
    if "брадок" in lowered:
        return True

    # Упоминание Telegram username бота
    if BOT_USERNAME:
        if f"@{BOT_USERNAME}" in lowered:
            return True

    # Ответ на сообщение самого бота
    reply = message.get("reply_to_message", {})
    reply_user = reply.get("from", {})

    if reply_user.get("is_bot"):
        if BOT_USERNAME and reply_user.get("username", "").lower() == BOT_USERNAME:
            return True

    return False


def get_history(chat_id):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []

    return chat_histories[chat_id]


def ask_bradok(chat_id):
    history = get_history(chat_id)

    result = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *history
        ],
        temperature=0.9,
        max_tokens=700
    )

    answer = result.choices[0].message.content

    if not answer:
        answer = "Бля, у меня мозг завис. Повтори 😂"

    history.append({
        "role": "assistant",
        "content": answer
    })

    chat_histories[chat_id] = history[-MAX_HISTORY:]

    return answer


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
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")

    if not text or not chat_id:
        return jsonify(ok=True)

    # Не обрабатываем команды
    if text.startswith("/"):
        return jsonify(ok=True)

    # Получаем имя отправителя
    name = get_user_name(message)

    # Определяем, обращаются ли к Брадку
    addressed = is_addressed(message, text, chat_type)

    # Сохраняем сообщения групповой переписки
    # даже если Брадка напрямую не спрашивали
    history = get_history(chat_id)

    history.append({
        "role": "user",
        "content": f"{name}: {text}"
    })

    chat_histories[chat_id] = history[-MAX_HISTORY:]

    # В группе отвечаем только на обращения
    if chat_type in ("group", "supergroup") and not addressed:
        return jsonify(ok=True)

    try:
        answer = ask_bradok(chat_id)

        send_message(
            chat_id,
            answer,
            reply_to=message.get("message_id")
        )

    except Exception:
        logger.exception("Message processing error")

        try:
            send_message(
                chat_id,
                "Бля, небольшая техническая заминка. Попробуй ещё раз 😂",
                reply_to=message.get("message_id")
            )
        except Exception:
            logger.exception("Telegram send error")

    return jsonify(ok=True)


# Настраиваем Telegram при запуске
try:
    get_bot_username()
    setup_webhook()
except Exception:
    logger.exception("Startup setup failed")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
