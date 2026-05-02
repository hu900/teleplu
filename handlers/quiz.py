"""
handlers/quiz.py — معالج الاختبار الكامل

إصلاحات وتحسينات v2:
  - [BUG FIX] _finish_quiz يحفظ user_id الصحيح (كان يحفظ chat_id)
  - [BUG FIX] progress bar يعرض عدد صحيح من البلوكات (total بدلاً من total-1)
  - [BUG FIX] cached PDF ينشئ chunks إذا لم تكن موجودة (مهم بعد فشل جلسة سابقة)
  - زر "اختبار جديد" في نهاية الاختبار
  - send_chat_action (typing...) أثناء المعالجة الطويلة
  - عرض explanation بعد كل إجابة (إذا توفر)
  - التحقق من طول اسم المادة
  - دعم مجموعات Telegram (تحفظ user_id لا chat_id)
"""
import hashlib
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes, ConversationHandler

import db
from config import MAX_PDF_SIZE_MB, MAX_WRONG_SHOWN
from services.chunk_service import prepare_chunks
from services.language_service import detect_text_language, get_language_label
from services.pdf_service import extract_text_from_pdf
from services.quiz_service import generate_questions

logger = logging.getLogger(__name__)

# ─── States ───────────────────────────────────────────────────────────────────
SUBJECT, PDF_WAIT, QUESTION_COUNT, QUIZ = range(4)

# ─── /newquiz ─────────────────────────────────────────────────────────────────

async def new_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    db.save_user(user.id, user.username)
    context.user_data.clear()
    context.user_data["user_id"] = user.id  # ✅ FIX: تخزين ID الصحيح

    await update.message.reply_text(
        "📚 *اختبار جديد*\n\n"
        "أرسل اسم المادة أو الموضوع الذي تريد الاختبار فيه:",
        parse_mode="Markdown",
    )
    return SUBJECT


# ─── استقبال اسم المادة ───────────────────────────────────────────────────────

async def receive_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subject = update.message.text.strip()

    if not subject:
        await update.message.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً. أرسل اسم المادة:")
        return SUBJECT

    if len(subject) > 100:
        await update.message.reply_text("⚠️ اسم المادة طويل جداً (الحد 100 حرف). أرسل اسماً أقصر:")
        return SUBJECT

    context.user_data["subject"] = subject

    await update.message.reply_text(
        f"✅ المادة: *{subject}*\n\n"
        f"الآن أرسل ملف PDF للاختبار أو المحتوى التعليمي\n"
        f"_(الحد الأقصى: {MAX_PDF_SIZE_MB} ميجابايت)_",
        parse_mode="Markdown",
    )
    return PDF_WAIT


# ─── استقبال ملف PDF ──────────────────────────────────────────────────────────

async def receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document

    if not (doc.mime_type == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf")):
        await update.message.reply_text("⚠️ يرجى إرسال ملف PDF فقط.")
        return PDF_WAIT

    max_bytes = MAX_PDF_SIZE_MB * 1024 * 1024
    if doc.file_size and doc.file_size > max_bytes:
        await update.message.reply_text(
            f"⚠️ الملف كبير جداً ({doc.file_size // (1024 * 1024)} ميجابايت).\n"
            f"الحد الأقصى: {MAX_PDF_SIZE_MB} ميجابايت."
        )
        return PDF_WAIT

    msg = await update.message.reply_text("⏳ جاري تحميل الملف...")

    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

        tg_file    = await doc.get_file()
        file_bytes = bytes(await tg_file.download_as_bytearray())
        file_hash  = hashlib.md5(file_bytes).hexdigest()

        # ─── التحقق من الكاش ─────────────────────────────────────────────
        cached = db.get_pdf_cache(file_hash)
        if cached:
            text       = cached["extracted_text"]
            language   = cached["language"]
            lang_label = get_language_label(language)
            logger.info("📦 PDF من الكاش: %s", file_hash[:8])
        else:
            await msg.edit_text("⏳ جاري استخراج النص من PDF...")
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

            text = extract_text_from_pdf(file_bytes)

            if not text or len(text.strip()) < 80:
                await msg.edit_text(
                    "❌ لم يتمكن من استخراج نص من الملف.\n\n"
                    "تأكد أن الـ PDF يحتوي على نص قابل للنسخ وليس صوراً ممسوحة ضوئياً."
                )
                return PDF_WAIT

            language   = detect_text_language(text)
            lang_label = get_language_label(language)
            db.upsert_pdf_cache(file_hash, doc.file_name, language, text)

        # ─── إنشاء chunks (للجديد والكاش معاً) ──────────────────────────
        # ✅ FIX: نتحقق دائماً وليس فقط للملفات الجديدة
        if db.get_chunk_count(file_hash) == 0:
            await msg.edit_text("⏳ جاري تحليل المحتوى وتقطيعه...")
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            chunks = prepare_chunks(file_hash, text, language)
            if chunks:
                db.save_chunks(file_hash, chunks)
                logger.info("✅ %d chunk للملف %s", len(chunks), file_hash[:8])

        # ─── حفظ بيانات الجلسة ────────────────────────────────────────────
        context.user_data["file_hash"] = file_hash
        context.user_data["language"]  = language
        context.user_data["file_name"] = doc.file_name or "ملف"

        # ─── اختيار عدد الأسئلة ───────────────────────────────────────────
        lang_label = get_language_label(language)
        keyboard = [
            [
                InlineKeyboardButton("5️⃣  أسئلة",    callback_data="qcount|5"),
                InlineKeyboardButton("🔟 أسئلة",     callback_data="qcount|10"),
            ],
            [
                InlineKeyboardButton("1️⃣5️⃣ سؤالاً",  callback_data="qcount|15"),
                InlineKeyboardButton("2️⃣0️⃣ سؤالاً",  callback_data="qcount|20"),
            ],
        ]
        await msg.edit_text(
            f"✅ *تم تحليل الملف بنجاح!*\n\n"
            f"📄 {doc.file_name or 'الملف'}\n"
            f"🌐 اللغة المكتشفة: {lang_label}\n\n"
            f"كم سؤالاً تريد في الاختبار؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return QUESTION_COUNT

    except Exception as e:
        logger.error("خطأ في معالجة PDF: %s", e, exc_info=True)
        await msg.edit_text(
            "❌ حدث خطأ أثناء معالجة الملف.\n"
            "حاول مرة أخرى أو أرسل ملفاً مختلفاً."
        )
        return PDF_WAIT


# ─── اختيار عدد الأسئلة ──────────────────────────────────────────────────────

async def choose_question_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    count     = int(query.data.split("|")[1])
    file_hash = context.user_data.get("file_hash", "")
    language  = context.user_data.get("language", "arabic")

    await query.edit_message_text(f"⏳ جاري توليد {count} سؤالاً...")
    await context.bot.send_chat_action(query.message.chat_id, ChatAction.TYPING)

    try:
        # جلب chunks (الأكثر تنوعاً حسب عدد الأسئلة)
        chunks_limit = max(3, count // 3 + 2)
        chunks = db.sample_chunks(file_hash, limit=chunks_limit)

        if chunks:
            text_content = "\n\n---\n\n".join(c["chunk_text"] for c in chunks)
            db.mark_chunks_used([c["id"] for c in chunks])
        else:
            cached = db.get_pdf_cache(file_hash)
            text_content = cached["extracted_text"] if cached else ""

        if not text_content:
            await query.edit_message_text("❌ لم يُعثر على محتوى. حاول رفع الملف مجدداً.")
            return ConversationHandler.END

        questions = await generate_questions(text_content, count=count, language=language)

        if not questions:
            await query.edit_message_text(
                "❌ تعذّر توليد الأسئلة من هذا المحتوى.\n"
                "تأكد أن الملف يحتوي على محتوى تعليمي كافٍ."
            )
            return ConversationHandler.END

        context.user_data["questions"]   = questions
        context.user_data["current_idx"] = 0
        context.user_data["score"]       = 0
        context.user_data["wrong"]       = []

        actual = len(questions)
        note   = f" _(وجدنا {actual} سؤالاً فقط)_" if actual < count else ""
        await query.edit_message_text(
            f"✅ تم توليد *{actual}* سؤالاً!{note}\n\n"
            f"يبدأ الاختبار الآن... حظاً موفقاً! 🎯",
            parse_mode="Markdown",
        )
        return await _send_question(query.message.chat_id, context)

    except Exception as e:
        logger.error("خطأ في توليد الأسئلة: %s", e, exc_info=True)
        await query.edit_message_text("❌ حدث خطأ أثناء توليد الأسئلة. حاول مرة أخرى.")
        return ConversationHandler.END


# ─── معالجة الإجابة ───────────────────────────────────────────────────────────

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    choice_idx = int(query.data.split("|")[1])
    idx        = context.user_data["current_idx"]
    questions  = context.user_data["questions"]
    q          = questions[idx]
    options    = q.get("options", [])
    correct_idx = q.get("correct_index", 0)
    is_correct  = (choice_idx == correct_idx)

    if is_correct:
        context.user_data["score"] += 1
        result_text = "✅ *إجابة صحيحة!*"
    else:
        correct_text = options[correct_idx] if correct_idx < len(options) else "—"
        result_text  = f"❌ *إجابة خاطئة!*\nالإجابة الصحيحة: _{correct_text}_"
        context.user_data["wrong"].append({
            "question":    q["question"],
            "your_answer": options[choice_idx] if choice_idx < len(options) else "—",
            "correct":     correct_text,
        })

    # إضافة explanation إذا توفّر
    explanation = q.get("explanation", "")
    if explanation:
        result_text += f"\n\n💡 _{explanation}_"

    score = context.user_data["score"]
    done  = idx + 1
    total = len(questions)
    await query.edit_message_text(
        f"{result_text}\n\n_النتيجة حتى الآن: {score}/{done}_",
        parse_mode="Markdown",
    )

    context.user_data["current_idx"] += 1

    if context.user_data["current_idx"] >= total:
        return await _finish_quiz(query.message.chat_id, context)

    return await _send_question(query.message.chat_id, context)


# ─── دوال مساعدة ──────────────────────────────────────────────────────────────

async def _send_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> int:
    idx       = context.user_data["current_idx"]
    questions = context.user_data["questions"]
    total     = len(questions)
    q         = questions[idx]
    options   = q.get("options", [])

    # تخطي الأسئلة بدون خيارات
    if not options:
        logger.warning("تخطي سؤال بدون خيارات — index %d", idx)
        context.user_data["current_idx"] += 1
        if context.user_data["current_idx"] >= total:
            return await _finish_quiz(chat_id, context)
        return await _send_question(chat_id, context)

    ar_labels = ["أ", "ب", "ج", "د", "هـ"]
    keyboard  = []
    for i, opt in enumerate(options[:5]):
        label    = ar_labels[i] if i < len(ar_labels) else str(i + 1)
        btn_text = f"{label}) {opt[:55]}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"ans|{i}")])

    # ✅ FIX: progress bar يعرض total بلوكات (كانت total-1)
    filled   = "▓" * idx
    current  = "🔵"
    empty    = "░" * (total - idx - 1)
    progress = filled + current + empty   # دائماً total بلوك

    text = (
        f"📝 *سؤال {idx + 1} من {total}*\n"
        f"`{progress}`\n\n"
        f"{q['question']}"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return QUIZ


async def _finish_quiz(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> int:
    score     = context.user_data["score"]
    questions = context.user_data["questions"]
    total     = len(questions)
    subject   = context.user_data.get("subject", "غير محدد")
    language  = context.user_data.get("language", "arabic")
    wrong     = context.user_data.get("wrong", [])
    # ✅ FIX: استخدام user_id المخزن (لا chat_id)
    user_id   = context.user_data.get("user_id", chat_id)
    pct       = round(score / total * 100) if total > 0 else 0

    if pct >= 90:
        grade = "🏆 ممتاز"
    elif pct >= 75:
        grade = "🥈 جيد جداً"
    elif pct >= 60:
        grade = "🥉 جيد"
    elif pct >= 50:
        grade = "⚠️ مقبول"
    else:
        grade = "❌ يحتاج مراجعة"

    text = (
        f"🎯 *انتهى الاختبار!*\n\n"
        f"📚 المادة: *{subject}*\n"
        f"✅ الصحيح: *{score}* / {total}\n"
        f"📊 النسبة: *{pct}%*\n"
        f"التقييم: {grade}\n"
    )

    # ملخص الأخطاء
    if wrong:
        shown = wrong[:MAX_WRONG_SHOWN]
        text += f"\n\n❌ *الأخطاء ({len(wrong)} سؤال):*\n"
        for i, w in enumerate(shown, 1):
            q_short  = w["question"][:80] + ("…" if len(w["question"]) > 80 else "")
            text    += f"\n*{i}.* {q_short}\n   ✅ _{w['correct']}_\n"
        if len(wrong) > MAX_WRONG_SHOWN:
            text += f"\n_...و {len(wrong) - MAX_WRONG_SHOWN} أخطاء أخرى_"

    # زر اختبار جديد
    keyboard = [[InlineKeyboardButton("🔄 اختبار جديد", callback_data="restart_quiz")]]

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # ✅ FIX: حفظ user_id الصحيح
    db.save_result(user_id, subject, score, total, language)
    logger.info("✅ نتيجة: user=%s subject=%s %d/%d (%d%%)", user_id, subject, score, total, pct)

    context.user_data.clear()
    return ConversationHandler.END


async def restart_from_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يُشغَّل عند ضغط زر 'اختبار جديد' في نهاية الاختبار."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    # إعادة تشغيل new_quiz عبر رسالة وهمية
    user = update.effective_user
    db.save_user(user.id, user.username)
    context.user_data.clear()
    context.user_data["user_id"] = user.id

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            "📚 *اختبار جديد*\n\n"
            "أرسل اسم المادة أو الموضوع الذي تريد الاختبار فيه:"
        ),
        parse_mode="Markdown",
    )
    return SUBJECT
