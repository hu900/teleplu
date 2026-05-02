from telegram import Update
from telegram.ext import ContextTypes
from db import save_user

WELCOME_TEXT = (
    "أهلاً بك في بوت محاكي الاختبارات 🎓\n\n"
    "هذا البوت يحوّل ملفات PDF إلى اختبارات اختيار من متعدد.\n"
    "لغة الاختبار تُحدَّد تلقائيًا حسب لغة الملف.\n"
    "ويستخدم OpenAI بنموذج اقتصادي لتوليد الأسئلة.\n\n"
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

