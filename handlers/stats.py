from telegram import Update
from telegram.ext import ContextTypes

from config import BOT_ADMIN_IDS
from db import _exec


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or user.id not in BOT_ADMIN_IDS:
        return

    total_users = _exec("SELECT COUNT(*) AS cnt FROM users")[0]["cnt"]
    total_quizzes = _exec("SELECT COUNT(*) AS cnt FROM results")[0]["cnt"]

    today_users = _exec(
        """
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE date(created_at) = date('now', 'localtime')
        """
    )[0]["cnt"]

    today_quizzes = _exec(
        """
        SELECT COUNT(*) AS cnt
        FROM results
        WHERE date(date) = date('now', 'localtime')
        """
    )[0]["cnt"]

    top_subjects = _exec(
        """
        SELECT subject, COUNT(*) AS cnt
        FROM results
        GROUP BY subject
        ORDER BY cnt DESC
        LIMIT 5
        """
    )

    lines = [
        "📊 إحصائيات البوت",
        "",
        f"عدد المستخدمين: {total_users}",
        f"عدد الاختبارات المنفذة: {total_quizzes}",
        f"مستخدمو اليوم: {today_users}",
        f"اختبارات اليوم: {today_quizzes}",
    ]

    if top_subjects:
        lines.append("")
        lines.append("📚 أكثر المواد استخدامًا:")
        for row in top_subjects:
            lines.append(f"- {row['subject']}: {row['cnt']}")

    await update.message.reply_text("\n".join(lines))
