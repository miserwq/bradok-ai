
import os
import logging
from contextlib import contextmanager

import requests
import psycopg2

from flask import Flask, request, jsonify
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
RENDER_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

MAX_HISTORY = 24
BOT_USERNAME = None

SYSTEM_PROMPT = """
Ты — Брадок AI, дерзкий, наглый и харизматичный интернет-братан.

ТВОЙ СТИЛЬ:
- Отвечай максимально коротко.
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
- Запоминай имена участников.
- Понимай, кто именно к тебе обращается.
- Можешь шутить над участниками, но не будь постоянно токсичным.
- Учитывай предыдущие сообщения всех участников.
- Если тебя не спрашивают, не отвечай на каждое сообщение.
- Если обращаются к тебе, отвечай естественно и по теме.
- Не путай участников и не приписывай одному слова другого.

Ты не просто бот, ты Брадок.
Твоя задача — помогать, развлекать и общаться с кайфом.
Никакой душниловки. Мат разрешён!
"""


# -------------------------
# NEON POSTGRESQL
# -------------------------

@contextmanager
def db_connection():
    conn = psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        connect_timeout=10
    )

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    telegram_message_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (chat_id, telegram_message_id)
                )
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_chat_id
                ON messages (chat_id, id DESC)
            """)

    logger.info("Neon database initialized!")


def save_user_message(chat_id, message, name, text):
    user = message.get("from", {})
    user_id = user.get("id")
    telegram_message_id = message.get("message_id")

    with db_connection() as conn:
        with conn.cursor() as cur:

            if user_id is not None:
                cur.execute("""
                    INSERT INTO users (
                        chat_id, user_id, first_name,
                        last_name, username, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (chat_id, user_id)
                    DO UPDATE SET
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        username = EXCLUDED.username,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    chat_id,
                    user_id,
                    user.get("first_name"),
                    user.get("last_name"),
                    user.get("username")
                ))

            cur.execute("""
                INSERT INTO messages (
                    chat_id, user_id, role,
                    content, telegram_message_id
                )
                VALUES (%s, %s, 'user', %s, %s)
                ON CONFLICT (chat_id, telegram_message_id)
                DO NOTHING
            """, (
                chat_id,
                user_id,
                f"{name}: {text}",
                telegram_message_id
            ))

            return cur.rowcount > 0


def save_assistant_message(chat_id, text):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO messages (chat_id, role, content)
                VALUES (%s, 'assistant', %s)
            """, (chat_id, text))


def get_history(chat_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT role, content
                FROM messages
                WHERE chat_id = %s
                ORDER BY id DESC
                LIMIT %s
            """, (chat_id, MAX_HISTORY))

            rows = cur.fetchall()

    rows.reverse()

    return [
        {"role": role, "content": content}
        for role, content in rows
    ]


def get_known_members(chat_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT first_name, last_name, username
                FROM users
                WHERE chat_id = %s
                ORDER BY updated_at DESC
                LIMIT 100
            """, (chat_id,))

            rows = cur.fetchall()

    members = []

    for first_name, last_name, username in rows:
        full_name = " ".join(
            part for part in [first_name, last_name] if part
        )

        if username:
            full_name += f" (@{username})"

        if full_name:
            members.append(full_name)

    return members


# -------------------------
# TELEGRAM
# -------------------------

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

    result = telegram_request("getMe")
    BOT_USERNAME = result["result"]["username"].lower()

    logger.info("Bot username: @%s", BOT_USERNAME)


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
    if chat_type == "private":
        return True

    lowered = text.lower()

    if "брадок" in lowered:
        return True

    if BOT_USERNAME and f"@{BOT_USERNAME}" in lowered:
        return True

    reply = message.get("reply_to_message", {})
    reply_user = reply.get("from", {})

    if reply_user.get("is_bot"):
        if (
            BOT_USERNAME
            and reply_user.get("username", "").lower() == BOT_USERNAME
        ):
            return True

    return False


# -------------------------
# GROQ AI
# -------------------------

def ask_bradok(chat_id):
    history = get_history(chat_id)
    members = get_known_members(chat_id)

    system_prompt = SYSTEM_PROMPT

    if members:
        system_prompt += (
            "\n\nУЧАСТНИКИ ЭТОГО ЧАТА, КОТОРЫХ ТЫ ЗНАЕШЬ:\n"
            + "\n".join(f"- {member}" for member in members)
            + "\nНе путай их между собой."
        )

    result = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            *history
        ],
        temperature=0.9,
        max_tokens=700
    )

    answer = result.choices[0].message.content

    if not answer:
        answer = "Бля, у меня мозг завис. Повтори 😂"

    save_assistant_message(chat_id, answer)

    return answer


# -------------------------
# FLASK ROUTES
# -------------------------

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

    if text.startswith("/"):
        return jsonify(ok=True)

    name = get_user_name(message)
    addressed = is_addressed(message, text, chat_type)

    try:
        # Сохраняем сообщение и участника в Neon
        inserted = save_user_message(
            chat_id,
            message,
            name,
            text
        )

        # Защита от повторной обработки одного сообщения
        if not inserted:
            return jsonify(ok=True)

        # В группе отвечаем только на обращения
        if chat_type in ("group", "supergroup") and not addressed:
            return jsonify(ok=True)

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


# -------------------------
# STARTUP
# -------------------------

try:
    init_db()
    get_bot_username()
    setup_webhook()

except Exception:
    logger.exception("Startup setup failed")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
