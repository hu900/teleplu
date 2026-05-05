from telegram import Update
from telegram.ext import ContextTypes

from db import get_stats, get_subject_stats


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_user_id = user.id

    stats = get_stats(tg_user_id)
    subjects = get_subject_stats(tg_user_id)

    total_quizzes = stats.get("total_quizzes", 0) or 0
    total_correct = stats.get("total_correct", 0) or 0
    total_questions = stats.get("total_questions", 0) or 0
    avg_pct = stats.get("avg_pct")
    best_pct = stats.get("best_pct")
    worst_pct = stats.get("worst_pct")

    avg_pct_text = f"{avg_pct}%" if avg_pct is not None else "—"
    best_pct_text = f"{best_pct}%" if best_pct is not None else "—"
    worst_pct_text = f"{worst_pct}%" if worst_pct is not None else "—"

    lines = [
        "📊 إحصائياتك",
        "",
        f"عدد الاختبارات: {total_quizzes}",
        f"إجمالي الإجابات الصحيحة: {total_correct}",
        f"إجمالي الأسئلة: {total_questions}",
        f"متوسط الأداء: {avg_pct_text}",
        f"أفضل نتيجة: {best_pct_text}",
        f"أقل نتيجة: {worst_pct_text}",
    ]

    if subjects:
        lines.append("")
        lines.append("📚 حسب المادة:")
        for item in subjects[:5]:
            subject = item.get("subject", "بدون اسم")
            attempts = item.get("attempts", 0)
            subj_avg = item.get("avg_pct")
            subj_best = item.get("best_pct")

            subj_avg_text = f"{subj_avg}%" if subj_avg is not None else "—"
            subj_best_text = f"{subj_best}%" if subj_best is not None else "—"

            lines.append(
                f"- {subject}: {attempts} محاولة، متوسط {subj_avg_text}، أفضل {subj_best_text}"
            )

    await update.message.reply_text("\n".join(lines))
