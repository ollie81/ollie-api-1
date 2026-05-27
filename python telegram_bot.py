from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from app import get_ollie_response, detect_language, detect_emotion, OllieDB, supabase
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("7954899561:AAGwbDbtuug56oxEc09WpwFisdwGJ2zgqqY")
db = OllieDB(supabase)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    await update.message.reply_text(
        f"hey {user.first_name}! ollie's here 🔥\n\n"
        f"i'm your gen z best friend. text me anything.\n"
        f"speak any language - i'll understand 💙\n\n"
        f"type /help for commands"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🗣️ i speak: english, kinyarwanda, swahili, french, korean\n"
        "💾 i remember what matters\n"
        "❤️ always here for you\n\n"

        "just talk to me like a friend"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = user.first_name.lower()
    user_input = update.message.text

    # Get or create user in Supabase
    db_user = db.get_or_create_user(username)
    user_id = db_user["id"]

    # Create or get session
    if 'session_id' not in context.user_data:
        session = db.start_session(user_id)
        context.user_data['session_id'] = session["id"]
    session_id = context.user_data['session_id']

    # Detect language and emotion
    language = detect_language(user_input)
    emotion, emotion_score = detect_emotion(user_input)

    # Build history from context
    history = context.user_data.get("history", [])
    history.append({"role": "user", "content": user_input})

    # Get Ollie's response
    reply = get_ollie_response(user_input, language, emotion, history)

    # Save to history
    history.append({"role": "assistant", "content": reply})
    if len(history) > 20:
        history = history[-20:]
    context.user_data["history"] = history

    # Save to Supabase
    db.save_message(user_id, session_id, user_input, "user", emotion_score)
    db.save_message(user_id, session_id, reply, "ollie", 0.0)

    # Send reply
    await update.message.reply_text(reply)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Ollie is live on Telegram! 🔥")
    app.run_polling()

if __name__ == "__main__":
    main()