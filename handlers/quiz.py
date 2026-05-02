"""
handlers/quiz.py — معالج محادثة الاختبار

الحالات:
  SUBJECT        → المستخدم يكتب اسم المادة
  PDF_WAIT       → المستخدم يرسل ملف PDF
  QUESTION_COUNT → المستخدم يختار عدد الأسئلة
  QUIZ           → المستخدم يجيب على الأسئلة

تحسينات v3:
- إضافة retry_same_pdf_handler: إعادة الاختبار بنفس الـ PDF وأسئلة جديدة
  بدون الحاجة لإرسال الملف مجدداً.
- حفظ pdf_text و language و subject و question_count في user_data
  بعد معالجة الـ PDF لاستخدامها عند إعادة التشغيل.
- زر "🔁 أسئلة جديدة بنفس الملف" يظهر في رسالة النتيجة النهائية.
"""
import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from services.language_service import detect_language
from services.pdf_service import extract_text_from_pdf
from services.quiz_service import generate_questions

logger = logging.getLogger(__name__)

# ─── States ───────────────────────────────────────────────────────────────────
SUBJECT, PDF_WAIT, QUESTION_COUNT, QUIZ = range(4)

# ─── خيارات عدد الأسئلة المتاحة ──────────────────────────────────────────────
QUESTION_COUNT_OPTIONS = [5, 10, 15, 20]


# ══════════════════════════════════════════════════════════════════════════════
# 1. بدء محادثة جديدة — /newquiz
# ══════════════════════════════════════════════════════════════════════════════

async def new_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نقطة الدخول: يطلب من المستخدم كتابة اسم المادة."""
    context.user_data.clear()
    await update.message.reply_text(
        "📚 *اختبار جديد*\n\n"
        "اكتب اسم المادة أو الموضوع الذي تريد الاختبار فيه:",
        parse_mode="Markdown",
    )
    return SUBJECT


# ══════════════════════════════════════════════════════════════════════════════
# 2. استقبال اسم المادة
# ══════════════════════════════════════════════════════════════════════════════

async def receive_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يحفظ اسم المادة ويطلب رفع ملف PDF."""
    subject = update.message.text.strip()
    if not subject:
        await update.message.reply_text("⚠️ يرجى كتابة اسم المادة.")
        return SUBJECT

    context.user_data["subject"] = subject
    await update.message.reply_text(
        f"✅ المادة: *{subject}*\n\n"
        "أرسل ملف PDF الآن 📄",
        parse_mode="Markdown",
    )
    return PDF_WAIT


# ══════════════════════════════════════════════════════════════════════════════
# 3. استقبال ملف PDF
# ══════════════════════════════════════════════════════════════════════════════

async def receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يستخرج النص من الـ PDF ويعرض خيارات عدد الأسئلة."""
    doc = update.message.document

    # التحقق من نوع الملف
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("⚠️ يرجى إرسال ملف PDF فقط.")
        return PDF_WAIT

    processing_msg = await update.message.reply_text("⏳ جارٍ معالجة الملف...")

    try:
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        pdf_text = extract_text_from_pdf(bytes(file_bytes))
    except Exception as e:
        logger.error("خطأ في استخراج النص من PDF: %s", e)
        await processing_msg.edit_text("❌ تعذّر قراءة الملف. تأكد أنه PDF صحيح وحاول مجدداً.")
        return PDF_WAIT

    if not pdf_text or len(pdf_text.strip()) < 50:
        await processing_msg.edit_text(
            "⚠️ الملف لا يحتوي على نص كافٍ.\n"
            "تأكد أن الـ PDF يحتوي على نص قابل للقراءة (ليس صورة فقط)."
        )
        return PDF_WAIT

    # ── حفظ البيانات في user_data للاستخدام لاحقاً (retry_same_pdf) ──────────
    language = detect_language(pdf_text)
    context.user_data["pdf_text"]  = pdf_text
    context.user_data["language"]  = language
    # subject محفوظ مسبقاً في receive_subject

    await processing_msg.edit_text(
        f"✅ تم استخراج النص بنجاح!\n"
        f"🌐 اللغة المكتشفة: {'عربي 🇸🇦' if language == 'arabic' else 'إنجليزي 🇬🇧'}\n\n"
        "كم سؤالاً تريد في الاختبار؟"
    )

    # أزرار اختيار عدد الأسئلة
    keyboard = [
        [InlineKeyboardButton(f"{n} أسئلة", callback_data=f"qcount|{n}")]
        for n in QUESTION_COUNT_OPTIONS
    ]
    await update.message.reply_text(
        "اختر عدد الأسئلة:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return QUESTION_COUNT


# ══════════════════════════════════════════════════════════════════════════════
# 4. اختيار عدد الأسئلة
# ══════════════════════════════════════════════════════════════════════════════

async def choose_question_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يحفظ عدد الأسئلة ويبدأ توليدها."""
    query = update.callback_query
    await query.answer()

    count = int(query.data.split("|")[1])
    context.user_data["question_count"] = count

    pdf_text = context.user_data.get("pdf_text", "")
    language = context.user_data.get("language", "arabic")

    await query.message.edit_text(f"⏳ جارٍ توليد {count} سؤال...")

    questions = await generate_questions(
        text_content=pdf_text,
        count=count,
        language=language,
    )

    if not questions:
        await query.message.edit_text(
            "❌ تعذّر توليد الأسئلة.\n"
            "حاول مرة أخرى أو ارفع ملفاً مختلفاً."
        )
        return ConversationHandler.END

    # تهيئة حالة الاختبار
    context.user_data["questions"]     = questions
    context.user_data["current_q"]     = 0
    context.user_data["score"]         = 0
    context.user_data["wrong_answers"] = []

    await query.message.edit_text(
        f"✅ تم توليد {len(questions)} سؤال!\nلنبدأ الاختبار 🚀"
    )

    return await _send_question(update, context)


# ══════════════════════════════════════════════════════════════════════════════
# 5. إرسال السؤال الحالي (دالة مساعدة)
# ══════════════════════════════════════════════════════════════════════════════

async def _send_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يرسل السؤال الحالي مع خيارات الإجابة."""
    questions  = context.user_data["questions"]
    current_q  = context.user_data["current_q"]
    total      = len(questions)

    if current_q >= total:
        return await _send_results(update, context)

    q    = questions[current_q]
    text = q["question"]
    opts = q["options"]

    # بناء أزرار الخيارات
    keyboard = [
        [InlineKeyboardButton(f"{chr(0x31 + i)}️⃣ {opt}", callback_data=f"ans|{i}")]
        for i, opt in enumerate(opts)
    ]

    msg = (
        f"📝 *السؤال {current_q + 1} من {total}*\n\n"
        f"{text}"
    )

    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return QUIZ


# ══════════════════════════════════════════════════════════════════════════════
# 6. معالجة إجابة المستخدم
# ══════════════════════════════════════════════════════════════════════════════

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يتحقق من الإجابة ويعرض التغذية الراجعة ثم ينتقل للسؤال التالي."""
    query = update.callback_query
    await query.answer()

    chosen_idx = int(query.data.split("|")[1])

    questions = context.user_data["questions"]
    current_q = context.user_data["current_q"]
    q         = questions[current_q]
    correct_i = q["correct_index"]
    opts      = q["options"]

    if chosen_idx == correct_i:
        context.user_data["score"] += 1
        feedback = f"✅ *إجابة صحيحة!*"
    else:
        context.user_data["wrong_answers"].append({
            "question":      q["question"],
            "your_answer":   opts[chosen_idx],
            "correct_answer":opts[correct_i],
        })
        feedback = (
            f"❌ *إجابة خاطئة!*\n"
            f"الإجابة الصحيحة: *{opts[correct_i]}*"
        )

    # إضافة الشرح إن وُجد
    explanation = q.get("explanation", "")
    if explanation:
        feedback += f"\n\n💡 _{explanation}_"

    await query.message.reply_text(feedback, parse_mode="Markdown")

    # الانتقال للسؤال التالي
    context.user_data["current_q"] += 1
    return await _send_question(update, context)


# ══════════════════════════════════════════════════════════════════════════════
# 7. عرض النتيجة النهائية
# ══════════════════════════════════════════════════════════════════════════════

async def _send_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعرض ملخص النتائج مع زرَّي إعادة الاختبار."""
    score         = context.user_data.get("score", 0)
    questions     = context.user_data.get("questions", [])
    wrong_answers = context.user_data.get("wrong_answers", [])
    subject       = context.user_data.get("subject", "")
    total         = len(questions)
    percentage    = round((score / total) * 100) if total else 0

    # تحديد الإيموجي حسب النتيجة
    if percentage >= 80:
        emoji = "🏆"
        grade = "ممتاز"
    elif percentage >= 60:
        emoji = "✅"
        grade = "جيد"
    elif percentage >= 40:
        emoji = "⚠️"
        grade = "مقبول"
    else:
        emoji = "❌"
        grade = "يحتاج مراجعة"

    result_text = (
        f"{emoji} *نتيجة الاختبار*\n"
        f"{'─' * 25}\n"
        f"📚 المادة: {subject}\n"
        f"🎯 النتيجة: {score} / {total} ({percentage}%)\n"
        f"📊 التقدير: {grade}\n"
    )

    # عرض الإجابات الخاطئة إن وجدت
    if wrong_answers:
        result_text += f"\n❌ *الأسئلة التي أخطأت فيها ({len(wrong_answers)}):*\n"
        for i, w in enumerate(wrong_answers, 1):
            result_text += (
                f"\n*{i}. {w['question']}*\n"
                f"   إجابتك: {w['your_answer']}\n"
                f"   الصحيحة: {w['correct_answer']}\n"
            )

    # ─── الأزرار ─────────────────────────────────────────────────────────────
    # ✅ زر "أسئلة جديدة بنفس الملف" + زر "اختبار جديد بملف مختلف"
    keyboard = [
        [InlineKeyboardButton("🔁 أسئلة جديدة بنفس الملف", callback_data="retry_same_pdf")],
        [InlineKeyboardButton("🔄 اختبار جديد (ملف مختلف)", callback_data="restart_quiz")],
    ]

    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=result_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# 8. زر "اختبار جديد" — يمسح كل شيء ويبدأ من الصفر
# ══════════════════════════════════════════════════════════════════════════════

async def restart_from_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يمسح بيانات الجلسة ويطلب اسم المادة من جديد."""
    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    await query.message.reply_text(
        "🔄 *بدء اختبار جديد*\n\n"
        "اكتب اسم المادة أو الموضوع:",
        parse_mode="Markdown",
    )
    return SUBJECT


# ══════════════════════════════════════════════════════════════════════════════
# 9. ✅ زر "أسئلة جديدة بنفس الملف" — يُعيد الاختبار بدون رفع PDF مجدداً
# ══════════════════════════════════════════════════════════════════════════════

async def retry_same_pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يُعيد توليد أسئلة جديدة من نفس الـ PDF المحفوظ في user_data،
    بدون الحاجة لإرسال الملف مرة أخرى.
    """
    query = update.callback_query
    await query.answer()

    # ── استرجاع بيانات الجلسة المحفوظة ──────────────────────────────────────
    pdf_text       = context.user_data.get("pdf_text")
    language       = context.user_data.get("language", "arabic")
    subject        = context.user_data.get("subject", "")
    question_count = context.user_data.get("question_count", 5)

    # إذا انتهت الجلسة (مثلاً بعد إعادة تشغيل البوت)
    if not pdf_text:
        await query.message.reply_text(
            "⚠️ انتهت صلاحية الجلسة، يرجى رفع الملف مجدداً.\n"
            "اكتب /newquiz للبدء."
        )
        return ConversationHandler.END

    await query.message.reply_text(
        f"⏳ جارٍ توليد {question_count} سؤال جديد من نفس الملف..."
    )

    # ── توليد أسئلة جديدة ────────────────────────────────────────────────────
    questions = await generate_questions(
        text_content=pdf_text,
        count=question_count,
        language=language,
    )

    if not questions:
        keyboard = [
            [InlineKeyboardButton("🔄 اختبار جديد (ملف مختلف)", callback_data="restart_quiz")]
        ]
        await query.message.reply_text(
            "❌ تعذّر توليد أسئلة جديدة.\n"
            "حاول مرة أخرى أو ارفع ملفاً مختلفاً.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END

    # ── إعادة تهيئة حالة الاختبار فقط (الملف والإعدادات تبقى محفوظة) ────────
    context.user_data["questions"]     = questions
    context.user_data["current_q"]     = 0
    context.user_data["score"]         = 0
    context.user_data["wrong_answers"] = []

    await query.message.reply_text(
        f"✅ تم توليد {len(questions)} سؤال جديد!\n"
        f"📚 المادة: {subject}\n"
        f"لنبدأ 🚀"
    )

    # ── إرسال السؤال الأول مباشرة ────────────────────────────────────────────
    return await _send_question(update, context)
