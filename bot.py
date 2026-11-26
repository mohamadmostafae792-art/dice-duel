import os
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL.startswith("https://"):
        await update.message.reply_text("مدیر بازی هنوز آدرس امن HTTPS را تنظیم نکرده است.")
        return
    keyboard = [[KeyboardButton("🎲 ورود به DICE DUEL", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "به DICE DUEL خوش آمدی!\\nبرای ورود به بازی دکمه زیر را بزن.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN را در فایل .env تنظیم کنید.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()

if __name__ == "__main__":
    main()
