from telegram import Update
from telegram.ext import ContextTypes
from db import save_user

WELCOME_TEXT = (
    "مرحبًا بك في بوت اختبارات PDF 📚\n\n"
    "أرسل ملف PDF وسأحوّله إلى اختبار تفاعلي يساعدك على المراجعة والتدرب بسرعة.\n\n"
    "الأوامر:\n" 
    "/start - الصفحة الرئيسية\n"
    "/help - المساعدة\n"
    "/newquiz - اختبار جديد\n"
    "/reports - تقاريري\n"
    "/cancel - إلغاء"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username or user.first_name)
    context.user_data.clear()
    await update.message.reply_text(WELCOME_TEXT)

