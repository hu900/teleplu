"""
handlers/reports.py — عرض تقارير المستخدم

تحسينات v2:
  - إحصاءات إجمالية في الأعلى (عدد الاختبارات، متوسط الدرجة، الأفضل/الأسوأ)
  - تفصيل حسب المادة
  - آخر N نتيجة مفصّلة
  - استخدام get_stats() وget_subject_stats() من db.py
"""
from telegram import Update
from telegram.ext import ContextTypes

import db


async def reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    stats   = db.get_stats(user_id)
    results = db.get_results(user_id, limit=10)

    if not results:
        await update.message.reply_text(
            "📭 لا توجد نتائج بعد.\n"
            "ابدأ اختبارك الأول بـ /newquiz"
        )
        return

    lines: list[str] = []

    # ─── الإحصاءات الإجمالية ─────────────────────────────────────────────────
    total_q  = stats.get("total_quizzes", 0)
    avg_pct  = stats.get("avg_pct")  or 0
    best_pct = stats.get("best_pct") or 0
    worst_pct= stats.get("worst_pct")or 0
    correct  = stats.get("total_correct", 0)
    questions= stats.get("total_questions", 0)

    lines.append("📊 *إحصاءاتك الإجمالية*\n")
    lines.append(f"🔢 عدد الاختبارات: *{total_q}*")
    lines.append(f"✅ إجمالي الإجابات الصحيحة: *{correct}* / {questions}")
    lines.append(f"📈 متوسط الدرجة: *{avg_pct}%*")
    lines.append(f"🏆 أفضل درجة: *{best_pct}%*  |  🔻 أدنى درجة: *{worst_pct}%*")

    # ─── تفصيل حسب المادة ────────────────────────────────────────────────────
    subject_stats = db.get_subject_stats(user_id)
    if len(subject_stats) > 1:
        lines.append("\n📚 *حسب المادة (متوسط الدرجة):*")
        for s in subject_stats[:8]:
            bar   = _pct_bar(s["avg_pct"])
            lines.append(
                f"• {s['subject']}: {bar} *{s['avg_pct']}%* "
                f"({s['attempts']} {"اختبار" if s["attempts"] == 1 else "اختبارات"})"
            )

    # ─── آخر النتائج ─────────────────────────────────────────────────────────
    lines.append(f"\n📋 *آخر {len(results)} نتائج:*")
    for r in results:
        subject = r["subject"]
        score   = r["score"]
        total   = r["total"]
        pct     = round(score / total * 100) if total else 0
        lang    = "🇸🇦" if r["language"] == "arabic" else "🇬🇧"
        date    = r["date"][:10]   # YYYY-MM-DD فقط
        emoji   = _grade_emoji(pct)
        lines.append(f"{emoji} {lang} {subject}: *{score}/{total}* ({pct}%) — {date}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _grade_emoji(pct: int) -> str:
    if pct >= 90: return "🏆"
    if pct >= 75: return "🥈"
    if pct >= 60: return "🥉"
    if pct >= 50: return "⚠️"
    return "❌"


def _pct_bar(pct: float, length: int = 8) -> str:
    """شريط تقدم نصي مصغّر."""
    filled = round((pct / 100) * length)
    return "▓" * filled + "░" * (length - filled)
