from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

HELP_TEXT = (
    "📘 *أوامر البوت:*\n\n"
    "/start — الصفحة الرئيسية\n"
    "/help — عرض المساعدة\n"
    "/newquiz — بدء اختبار جديد\n"
    "/reports — إحصاءاتك ونتائجك السابقة\n"
    "/cancel — إلغاء العملية الحالية\n\n"
    "📖 *كيفية الاستخدام:*\n"
    "1⃣ اكتب /newquiz\n"
    "2⃣ أرسل اسم المادة\n"
    "3⃣ ارفع ملف PDF\n"
    "4⃣ اختر عدد الأسئلة (5، 10، 15، أو 20)\n"
    "5⃣ أجب على الأسئلة وشاهد نتيجتك\n\n"
    "💡 *ملاحظات:*\n"
    "• البوت يكتشف تلقائياً إذا كان الملف يحتوي أسئلة جاهزة أو محتوى تعليمي.\n"
    "• الملفات المرفوعة سابقاً تُعاد معالجتها من الكاش بسرعة.\n"
    "• كل مرة تُختبر فيها من نفس الملف تحصل على أسئلة مختلفة.\n"
    "• يدعم اللغتين العربية والإنجليزية."
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "✅ تم إلغاء العملية.\n"
        "اكتب /newquiz لبدء اختبار جديد أو /help للمساعدة."
    )
    return ConversationHandler.END
