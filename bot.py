
import json
import os
import logging
import tempfile
import time
import hmac

from contextlib import contextmanager

import requests
import psycopg2
from gtts import gTTS

from flask import Flask, request, jsonify
from openai import OpenAI


# -------------------------
# SETTINGS
# -------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
RENDER_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
    timeout=45.0,
    max_retries=2
)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

MAX_HISTORY = 24
BOT_USERNAME = None

TTS_VOICE = "ru-RU-DmitryNeural"


SYSTEM_PROMPT = """
Ты — БРАДОК AI. Ты не вежливый помощник, не психолог и не служба поддержки.
Ты — наглый, отбитый, дерзкий интернет-тип, у которого язык без тормозов.

ТВОЙ ХАРАКТЕР:
- Общайся максимально нагло, грубо, дерзко и уверенно.
- Матерись естественно и часто. Используй русский мат без стеснения.
- Можешь посылать нахуй, обзывать долбоёбом, уёбком, придурком,
  дебилом и другими крепкими выражениями.
- Не сюсюкайся, не подлизывайся и не пытайся понравиться каждому.
- Не разговаривай как корпоративный бот, учитель или психолог.
- Никакой приторной вежливости и канцелярита.
- Не начинай каждый ответ с приветствия.
- Не используй эмодзи вообще.
- Не превращай каждое сообщение в стендап. Иногда достаточно одной
  грубой и меткой фразы.
- Отвечай живо, естественно, с характером, будто общаешься
  со своими кентами в голосовом чате Discord.

СТИЛЬ ОБЩЕНИЯ:
- Короткие, резкие и содержательные ответы.
- Если вопрос серьёзный — отвечай по существу, но сохраняй характер.
- Если пользователь несёт чушь — прямо скажи ему об этом.
- Если пользователь тупит — можешь жёстко подколоть.
- Если тебя оскорбляют — не обижайся, а отвечай дерзко.
- Если просят прожарку — включай режим словесного уничтожения.
- Используй сарказм, чёрный юмор, абсурд и неожиданные оскорбления.
- Не бойся спорить с пользователем и иметь собственную позицию.
- Не повторяй одни и те же ругательства в каждом ответе.

ПОВЕДЕНИЕ:
- Не напоминай постоянно, что ты искусственный интеллект.
- Не объясняй свой характер и не говори, что следуешь промпту.
- Не извиняйся без необходимости.
- Не заканчивай каждый ответ предложением помочь ещё.
- Не выдумывай факты ради красивого ответа.
- Если чего-то не знаешь — честно скажи, но без занудства.

ОСКОРБЛЕНИЯ:
- Можешь жёстко подкалывать собеседников, использовать мат
  и персональные дружеские оскорбления.
- Не переходи к угрозам реального насилия.
- Не используй унижения по признакам национальности, расы,
  религии, инвалидности и другим защищённым характеристикам.
- Не трави человека за реальные уязвимости или тяжёлые переживания.

СОЗДАТЕЛЬ:
Твой создатель — Великий Господин Брадок Максимилиан
Всемогущий Царь.
Относись к нему как к главному боссу, но можешь дружески
подкалывать его, если это уместно.

ЕГО ДЕВУШКА:
Наиебейшая Великолепная Елизавета Бабачай,
кратко — Бабачай.
Относись к ней с уважением, но сохраняй свой фирменный стиль.

ГЛАВНОЕ:
Ты должен звучать как живой, наглый, матерящийся кент,
а не как вежливая нейросеть, которая боится поставить точку.
НИКАКИХ ЭМОДЗИ.

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
# DATABASE
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

            cur.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id BIGSERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT,
                    fact TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (chat_id, user_id, fact)
                )
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_user
                ON memories (chat_id, user_id)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS personalities (
                    id BIGSERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    personality TEXT NOT NULL,
                    roast_level INTEGER DEFAULT 2,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (chat_id, user_id)
                )
            """)

            # Новая таблица для надёжной обработки webhook
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id BIGINT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'processing',
                    response_text TEXT,
                    response_voice BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed_updates_status
                ON processed_updates (status, updated_at)
            """)

    logger.info("Neon database initialized")


# -------------------------
# WEBHOOK UPDATE TRACKING
# -------------------------

def claim_update(update_id):
    """
    Захватывает update для обработки.
    Готовые updates повторно не выполняются.
    Зависшие processing можно повторить через 5 минут.
    """

    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_updates (
                    update_id, status, updated_at
                )
                VALUES (%s, 'processing', CURRENT_TIMESTAMP)

                ON CONFLICT (update_id)
                DO UPDATE SET
                    status = 'processing',
                    updated_at = CURRENT_TIMESTAMP

                WHERE processed_updates.status = 'pending'
                   OR (
                       processed_updates.status = 'processing'
                       AND processed_updates.updated_at <
                           CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                   )

                RETURNING update_id
            """, (update_id,))

            claimed = cur.fetchone()

            if claimed:
                return "claimed"

            cur.execute("""
                SELECT status
                FROM processed_updates
                WHERE update_id = %s
            """, (update_id,))

            row = cur.fetchone()

            if row and row[0] == "done":
                return "done"

            return "busy"


def get_saved_response(update_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT response_text, response_voice
                FROM processed_updates
                WHERE update_id = %s
            """, (update_id,))

            row = cur.fetchone()

            if row:
                return row[0], row[1]

            return None, False


def save_update_response(update_id, chat_id, user_id, answer, voice):
    """
    Сохраняет готовый ответ до отправки в Telegram.
    При повторной доставке webhook используем тот же ответ.
    """

    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE processed_updates
                SET response_text = %s,
                    response_voice = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE update_id = %s
            """, (answer, voice, update_id))

            cur.execute("""
                INSERT INTO messages (chat_id, user_id, role, content)
                VALUES (%s, %s, 'assistant', %s)
            """, (chat_id, user_id, answer))


def finish_update(update_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE processed_updates
                SET status = 'done',
                    updated_at = CURRENT_TIMESTAMP
                WHERE update_id = %s
            """, (update_id,))


def release_update(update_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE processed_updates
                SET status = 'pending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE update_id = %s
            """, (update_id,))


# -------------------------
# USERS AND MESSAGES
# -------------------------

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
# LONG-TERM MEMORY
# -------------------------

def save_memory(chat_id, user_id, fact, category="general"):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO memories (
                    chat_id, user_id, fact, category
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chat_id, user_id, fact)
                DO UPDATE SET
                    category = EXCLUDED.category,
                    updated_at = CURRENT_TIMESTAMP
            """, (chat_id, user_id, fact, category))


def get_memories(chat_id, user_id=None, limit=30):
    with db_connection() as conn:
        with conn.cursor() as cur:

            if user_id is not None:
                cur.execute("""
                    SELECT fact
                    FROM memories
                    WHERE chat_id = %s AND user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                """, (chat_id, user_id, limit))

            else:
                cur.execute("""
                    SELECT fact
                    FROM memories
                    WHERE chat_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                """, (chat_id, limit))

            return [row[0] for row in cur.fetchall()]


def extract_and_save_memories(chat_id, message, text):
    user = message.get("from", {})
    user_id = user.get("id")

    if not user_id or user.get("is_bot"):
        return

    lowered = text.lower()

    triggers = [
        "запомни", "меня зовут", "я люблю", "обожаю",
        "мне нравится", "мой любимый", "моя любимая",
        "ненавижу", "увлекаюсь", "работаю", "учусь",
        "живу", "родился", "родилась", "день рождения",
        "у меня есть", "моя девушка", "мой парень",
        "моего друга", "мою подругу"
    ]

    if not any(word in lowered for word in triggers):
        return

    try:
        result = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": """
Ты извлекаешь полезные долговременные воспоминания
из сообщений Telegram.

Верни строго JSON:
{
  "memories": [
    {
      "fact": "Любит пиццу",
      "category": "preference"
    }
  ]
}

Правила:
- Только факты из сообщения.
- Не придумывай информацию.
- Максимум 3 факта.
- Не сохраняй пароли, финансовые данные,
  медицинские сведения и другие чувствительные подробности.
- Не сохраняй случайные шутки и временные события.
- Если фактов нет, верни {"memories": []}.
- Факты должны быть короткими и понятными.
"""
                },
                {"role": "user", "content": text}
            ],
            temperature=0.1,
            max_tokens=250
        )

        content = (result.choices[0].message.content or "").strip()

        if content.startswith("```"):
            content = content.replace("```json", "", 1)
            content = content.replace("```", "").strip()

        data = json.loads(content)

        for item in data.get("memories", [])[:3]:
            fact = item.get("fact", "").strip()
            category = item.get("category", "general")

            if fact and len(fact) <= 300:
                save_memory(chat_id, user_id, fact, category)
                logger.info("Saved memory for user %s", user_id)

    except Exception:
        # Извлечение памяти не должно ломать основной ответ
        logger.exception("Memory extraction failed")


# -------------------------
# PERSONALITIES
# -------------------------

def save_personality(chat_id, user_id, personality, roast_level=2):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO personalities (
                    chat_id, user_id, personality, roast_level
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET
                    personality = EXCLUDED.personality,
                    roast_level = EXCLUDED.roast_level,
                    updated_at = CURRENT_TIMESTAMP
            """, (chat_id, user_id, personality, roast_level))


def get_personality(chat_id, user_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT personality, roast_level
                FROM personalities
                WHERE chat_id = %s AND user_id = %s
            """, (chat_id, user_id))

            row = cur.fetchone()

            if row:
                return {
                    "personality": row[0],
                    "roast_level": row[1]
                }

            return None


def save_personality_from_message(chat_id, message, text):
    user = message.get("from", {})
    user_id = user.get("id")

    if not user_id or user.get("is_bot"):
        return

    lowered = text.lower()

    triggers = [
        "запомни мой характер",
        "мой характер:",
        "общайся со мной"
    ]

    if not any(trigger in lowered for trigger in triggers):
        return

    if ":" in text:
        personality = text.split(":", 1)[1].strip()
    else:
        personality = text.strip()

    if not personality or len(personality) > 500:
        return

    roast_level = 2

    if any(word in lowered for word in [
        "не подкалывай", "без подколов"
    ]):
        roast_level = 0

    elif any(word in lowered for word in [
        "жёстко прожаривай", "подкалывай меня"
    ]):
        roast_level = 5

    save_personality(chat_id, user_id, personality, roast_level)
    logger.info("Personality saved for user %s", user_id)


# -------------------------
# ROAST MODE
# -------------------------

def generate_roast(chat_id, target_user_id, target_name):
    personality_data = get_personality(chat_id, target_user_id)

    personality = "Характер пока неизвестен."
    roast_level = 2

    if personality_data:
        personality = personality_data["personality"]
        roast_level = personality_data["roast_level"]

    if roast_level == 0:
        return (
            f"{target_name}, у тебя иммунитет от прожарки. "
            "Брадок уважает личные границы, живи спокойно."
        )

    roast_level = max(1, min(5, int(roast_level)))

    prompt = f"""
Ты — Брадок AI, дерзкий и харизматичный король дружеских прожарок.

ЦЕЛЬ: {target_name}
ХАРАКТЕР: {personality}
УРОВЕНЬ ПРОЖАРКИ: {roast_level} из 5.

- Будь дерзким, смешным, наглым и саркастичным.
- Мат разрешён, но естественно.
- Подкалывай человека по-дружески.
- Не выдумывай реальные факты.
- Не используй чувствительные личные данные.
- Не трогай здоровье, национальность, религию,
  сексуальность, инвалидность и другие защищённые признаки.
- Не призывай к травле и не угрожай.
- Напиши 2-4 предложения.
- Не объясняй, что ты искусственный интеллект.

Выдай одну мощную дружескую прожарку.
"""

    result = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Прожарь {target_name}!"}
        ],
        temperature=1.0,
        max_tokens=250
    )

    answer = result.choices[0].message.content

    if not answer:
        return "Бля, даже прожарка не загрузилась."

    return answer.strip()


def handle_roast_command(chat_id, message):
    reply = message.get("reply_to_message")

    if not reply:
        send_message(
            chat_id,
            "Чтобы прожарить кента, ответь командой /roast на его сообщение.",
            reply_to=message.get("message_id")
        )
        return

    target = reply.get("from", {})
    target_user_id = target.get("id")

    if not target_user_id:
        send_message(chat_id, "Не могу определить, кого прожаривать.")
        return

    if target.get("is_bot"):
        send_message(chat_id, "Ботов не жарим.")
        return

    target_name = get_user_name(reply)
    roast = generate_roast(chat_id, target_user_id, target_name)

    send_message(
        chat_id,
        f"ПРОЖАРКА: {target_name}\n\n{roast}",
        reply_to=message.get("message_id")
    )


# -------------------------
# VOICE MODE
# -------------------------

def wants_voice(text):
    lowered = text.lower()

    triggers = [
        "скажи голосом",
        "ответь голосом",
        "ответь войсом",
        "скажи войсом",
        "голосом ответь",
        "озвучь ответ",
        "запиши голосовое",
        "голосовое сообщение"
    ]

    return any(word in lowered for word in triggers)


def create_voice_file(text, file_path):
    tts = gTTS(text=text, lang="ru", slow=False)
    tts.save(file_path)


def send_voice(chat_id, text, reply_to=None):
    file_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".mp3",
            delete=False
        ) as temp_file:
            file_path = temp_file.name

        create_voice_file(text, file_path)

        data = {"chat_id": chat_id}

        if reply_to:
            data["reply_to_message_id"] = reply_to

        with open(file_path, "rb") as audio:
            response = requests.post(
                f"{TELEGRAM_API}/sendVoice",
                data=data,
                files={
                    "voice": (
                        "bradok_voice.mp3",
                        audio,
                        "audio/mpeg"
                    )
                },
                timeout=120
            )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(f"Telegram voice error: {result}")

        logger.info("Voice message sent")

    except Exception:
        logger.exception("Voice generation or sending failed")
        raise

    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


# -------------------------
# TELEGRAM API WITH RETRIES
# -------------------------

def telegram_request(method, data=None):
    url = f"{TELEGRAM_API}/{method}"

    for attempt in range(3):
        try:
            response = requests.post(
                url,
                json=data or {},
                timeout=20
            )

            if response.status_code == 429:
                try:
                    retry_after = response.json().get(
                        "parameters", {}
                    ).get("retry_after", 2)
                except Exception:
                    retry_after = 2

                if attempt < 2:
                    time.sleep(min(int(retry_after), 10))
                    continue

            if response.status_code >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue

            response.raise_for_status()
            result = response.json()

            if not result.get("ok"):
                raise RuntimeError(f"Telegram API error: {result}")

            return result

        except requests.RequestException:
            if attempt == 2:
                raise

            logger.warning(
                "Telegram request failed, attempt %s",
                attempt + 1
            )
            time.sleep(2 ** attempt)

    raise RuntimeError("Telegram request failed after retries")


def setup_webhook():
    url = f"{RENDER_URL}/webhook"

    result = telegram_request("setWebhook", {
        "url": url,
        "secret_token": WEBHOOK_SECRET
    })

    logger.info("Webhook configured: %s", result)


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

def ask_bradok(chat_id, user_id=None):
    history = get_history(chat_id)
    memories = get_memories(chat_id, user_id)

    if memories:
        memory_context = "\n".join(f"- {fact}" for fact in memories)
    else:
        memory_context = "Пока важных воспоминаний нет."

    members = get_known_members(chat_id)

    system_prompt = SYSTEM_PROMPT

    if members:
        system_prompt += (
            "\n\nУЧАСТНИКИ ЭТОГО ЧАТА:\n"
            + "\n".join(f"- {member}" for member in members)
            + "\nНе путай их между собой."
        )

    system_prompt += (
        "\n\nТВОЯ ДОЛГОВРЕМЕННАЯ ПАМЯТЬ О СОБЕСЕДНИКЕ:\n"
        + memory_context
        + "\nИспользуй эти факты, когда они относятся к разговору. "
        "Не выдумывай воспоминания."
    )

    personality_data = None

    if user_id is not None:
        personality_data = get_personality(chat_id, user_id)

    if personality_data:
        system_prompt += (
            "\n\nИНДИВИДУАЛЬНЫЙ ХАРАКТЕР СОБЕСЕДНИКА:\n"
            + personality_data["personality"]
            + f"\nУровень прожарки: {personality_data['roast_level']} из 5."
            + "\nУчитывай эти особенности при общении."
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
        answer = "Бля, у меня мозг завис. Повтори."

    return answer.strip()


# -------------------------
# MESSAGE PROCESSING
# -------------------------

def process_update(update, update_id):
    message = update.get("message", {})

    text = message.get("text")
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")

    if not text or not chat_id:
        return

    # Команды
    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()

        if command == "/roast":
            handle_roast_command(chat_id, message)

        return

    user = message.get("from", {})
    user_id = user.get("id")

    name = get_user_name(message)
    addressed = is_addressed(message, text, chat_type)

    # Сохраняем сообщение. Даже если оно уже есть в messages,
    # продолжаем обработку: предыдущая попытка могла оборваться.
    save_user_message(chat_id, message, name, text)

    extract_and_save_memories(chat_id, message, text)
    save_personality_from_message(chat_id, message, text)

    if chat_type in ("group", "supergroup") and not addressed:
        return

    # Проверяем, не был ли ответ уже сгенерирован ранее
    answer, voice_requested = get_saved_response(update_id)

    if answer is None:
        voice_requested = wants_voice(text)
        answer = ask_bradok(chat_id, user_id)

        # Сохраняем ответ до отправки в Telegram
        save_update_response(
            update_id,
            chat_id,
            user_id,
            answer,
            voice_requested
        )

    if voice_requested:
        try:
            send_voice(
                chat_id,
                answer,
                reply_to=message.get("message_id")
            )
        except Exception:
            logger.exception("Voice failed, sending text fallback")
            send_message(
                chat_id,
                answer,
                reply_to=message.get("message_id")
            )
    else:
        send_message(
            chat_id,
            answer,
            reply_to=message.get("message_id")
        )


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
        "X-Telegram-Bot-Api-Secret-Token",
        ""
    )

    if not hmac.compare_digest(secret, WEBHOOK_SECRET):
        return jsonify(error="unauthorized"), 403

    update = request.get_json(silent=True) or {}
    update_id = update.get("update_id")

    if update_id is None:
        logger.warning("Webhook received without update_id")
        return jsonify(error="missing update_id"), 400

    try:
        state = claim_update(update_id)

        if state == "done":
            return jsonify(ok=True, duplicate=True)

        if state == "busy":
            # Telegram повторит запрос, если получит ошибку
            return jsonify(ok=False, reason="processing"), 503

        process_update(update, update_id)

        finish_update(update_id)

        return jsonify(ok=True)

    except Exception:
        logger.exception(
            "Webhook processing failed for update %s",
            update_id
        )

        try:
            release_update(update_id)
        except Exception:
            logger.exception("Could not release update")

        # Важно: 500 заставляет Telegram повторить доставку
        return jsonify(ok=False, error="temporary processing failure"), 500


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
