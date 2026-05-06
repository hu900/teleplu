"""
main.py — نقطة الدخول الرئيسية للبوت

تحسينات v2:
- إضافة معالج زر "اختبار جديد" (restart_quiz)
- error handler محسّن مع معلومات أكثر
- graceful shutdown

تحسينات v3:
- إضافة زر "أسئلة جديدة بنفس الملف" (retry_same_pdf)
"""
import logging
import os
import traceback

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN
from db import init_db
from handlers.common import cancel_command, help_command
from handlers.quiz import (
    PDF_WAIT,
    QUESTION_COUNT,
    QUIZ,
    SUBJECT,
    choose_question_count,
    handle_answer,
    new_quiz,
    receive_pdf,
    receive_subject,
    restart_from_button,
    retry_same_pdf_handler,      # ← جديد
)
from handlers.reports import reports
from handlers.start import start
from handlers.stats import admin_stats

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ─── Error Handler ────────────────────────────────────────────────────────────

async def error_handler(update: object, context) -> None:
    tb = "".join(
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__)
    )
    logger.error("❌ خطأ غير متوقع:\n%s", tb)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ حدث خطأ غير متوقع.\n"
                "اكتب /cancel للبدء من جديد أو /help للمساعدة."
            )
        except Exception:
            pass


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("❌ TELEGRAM_BOT_TOKEN غير موجود في Environment Variables")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # ─── Conversation: Quiz ───────────────────────────────────────────────────
    quiz_conv = ConversationHandler(
        entry_points=[
            CommandHandler("newquiz", new_quiz),
            # زر "اختبار جديد" من نهاية الاختبار يُعيد تشغيل المحادثة من البداية
            CallbackQueryHandler(restart_from_button, pattern=r"^restart_quiz$"),
            # ✅ زر "أسئلة جديدة بنفس الملف" — يتجاوز مرحلة الـ PDF ويولّد أسئلة جديدة
            CallbackQueryHandler(retry_same_pdf_handler, pattern=r"^retry_same_pdf$"),
        ],
        states={
            SUBJECT:        [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_subject)],
            PDF_WAIT:       [MessageHandler(filters.Document.ALL, receive_pdf)],
            QUESTION_COUNT: [CallbackQueryHandler(choose_question_count, pattern=r"^qcount\|")],
            QUIZ:           [CallbackQueryHandler(handle_answer, pattern=r"^ans\|")],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
        conversation_timeout=None,
    )

    # ─── Handlers ─────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reports", reports))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(quiz_conv)
    app.add_handler(CommandHandler("cancel", cancel_command))

    # معالج الأزرار خارج المحادثة (بعد انتهاء ConversationHandler)
    app.add_handler(CallbackQueryHandler(restart_from_button,    pattern=r"^restart_quiz$"))
    app.add_handler(CallbackQueryHandler(retry_same_pdf_handler, pattern=r"^retry_same_pdf$"))  # ← جديد

    app.add_error_handler(error_handler)

    logger.info("🚀 البوت يعمل الآن...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
