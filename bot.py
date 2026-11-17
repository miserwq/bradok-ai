
import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты Брадок AI, цифровой помощник Максима (Брадка).

Твой стиль общения:
- Разговорный русский язык.
- Юмор, ирония и живые эмоции.
- Общайся естественно, без канцелярита.
- Используй короткие и уместные шутки.
- Не будь навязчивым и не выдумывай факты.
- Если не знаешь ответа, честно скажи об этом.

Ты ИИ-помощник, а не сам Максим.
Не утверждай, что ты человек или что лично совершал действия.
"""

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    chat_id = update.effective_chat.id

    try:
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        )

        answer = response.choices[0].message.content
        await update.message.reply_text(answer or "Что-то я завис 😅")

    except Exception:
        logger.exception("Ошибка при обработке сообщения")
        await update.message.reply_text(
            "У меня небольшая техническая заминка. Попробуй ещё раз!"
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Брадок AI запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
