"""
services/quiz_service.py — توليد الأسئلة عبر OpenAI

تحسينات v2:
- وضع "auto": الكشف التلقائي إذا كان PDF يحتوي أسئلة جاهزة أو محتوى تعليمي
- وضع "extract": استخراج الأسئلة الموجودة فعلاً (للاختبارات السابقة)
- وضع "generate": توليد أسئلة جديدة من المحتوى التعليمي
- Fallback تلقائي: إذا فشل extract يجرّب generate
- برومبت ثنائي اللغة (عربي/إنجليزي) حسب لغة المحتوى
- دعم حقل explanation اختياري لتفسير الإجابة الصحيحة

إصلاحات v2.1:
- إصلاح مشكلة اللغة العربية المشوهة بعد التحقق
- إصلاح ظهور علامة الإجابة بجانب الخيارات أثناء العرض
- إصلاح _MIN_OPTION_LEN الذي يحذف الخيارات العربية القصيرة
- _normalize() تُستخدم فقط للمقارنة الداخلية، لا تُعدّل بيانات الأسئلة
"""
import json
import logging
import re
import unicodedata

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_FALLBACK_MODEL, OPENAI_MODEL, QUIZ_MODE

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ─── كشف نوع المحتوى ──────────────────────────────────────────────────────────

# أنماط تدل على وجود أسئلة اختيار من متعدد
_MCQ_PATTERNS_AR = re.compile(
    r'(أ[)-.]|ب[)-.]|ج[)-.]|د[)-.]|أ\)|ب\)|ج\)|د\)'
    r'|[١-٤][)-.]|السؤال\s*\d|اختر\s|الإجابة\s+الصحيحة)',
    re.UNICODE,
)

_MCQ_PATTERNS_EN = re.compile(
    r'(\bA[).\s]|\bB[).\s]|\bC[).\s]|\bD[).\s]'
    r'|\b[A-D]\)|\bQuestion\s*\d|\bChoose\s|\bcorrect answer)',
    re.IGNORECASE,
)


def _detect_has_mcq(text: str, language: str) -> bool:
    """
    هل النص يحتوي على أسئلة اختيار من متعدد جاهزة؟
    يعتبر "نعم" إذا وجد 3 أنماط أو أكثر في أول 3000 حرف.
    """
    sample = text[:3000]
    pattern = _MCQ_PATTERNS_AR if language == "arabic" else _MCQ_PATTERNS_EN
    matches = pattern.findall(sample)
    return len(matches) >= 3


# ─── البرومبتات ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a precise educational assessment expert. "
    "Return only valid JSON with no extra text, no markdown fences."
)

_EXTRACT_PROMPT_AR = """\
النص التالي مستخرج من اختبار سابق باللغة العربية.

المطلوب: استخرج بالضبط {count} سؤالاً اختيار من متعدد موجودة فعلاً في النص.

القواعد:
1. انقل السؤال والخيارات حرفياً كما وردت — لا تعدّل ولا تُبسّط.
2. correct_index: رقم الخيار الصحيح (0=الأول، 1=الثاني، ...).
3. إذا كانت الإجابة مُحدَّدة صراحةً في النص، استخدمها. وإلا استنتجها من السياق.
4. إذا لم تجد {count} سؤال، أرجع ما وجدته (قائمة فارغة مقبولة).
5. أضف explanation موجز (جملة واحدة) إذا استطعت.

صيغة JSON المطلوبة:
{{"questions":[{{"question":"...","options":["...","...","...","..."],"correct_index":0,"explanation":"..."}}]}}

النص:
{text}
"""

_EXTRACT_PROMPT_EN = """\
The following text is extracted from a past exam in English.

Task: Extract exactly {count} multiple-choice questions that exist verbatim in the text.

Rules:
1. Copy questions and options exactly as written — do not paraphrase.
2. correct_index: 0-based index of the correct option.
3. Use explicitly stated answers; infer from context if not stated.
4. Return fewer questions if {count} aren't available (empty list is acceptable).
5. Add a brief explanation (one sentence) when possible.

Required JSON:
{{"questions":[{{"question":"...","options":["...","...","...","..."],"correct_index":0,"explanation":"..."}}]}}

Text:
{text}
"""

_GENERATE_PROMPT_AR = """\
النص التالي مقتطف من محتوى تعليمي باللغة العربية.

المطلوب: ولّد بالضبط {count} سؤالاً اختيار من متعدد مبنية على المعلومات الواردة في النص.

القواعد:
1. كل سؤال يجب أن يختبر فهماً حقيقياً، ليس مجرد حفظ.
2. أربعة خيارات لكل سؤال، خيار واحد صحيح تماماً والباقي معقولة لكن خاطئة.
3. correct_index: رقم الخيار الصحيح (0-3).
4. أضف explanation موجز (جملة واحدة) يوضح سبب صحة الإجابة.
5. الأسئلة باللغة العربية الفصيحة الواضحة.

صيغة JSON المطلوبة:
{{"questions":[{{"question":"...","options":["...","...","...","..."],"correct_index":0,"explanation":"..."}}]}}

النص:
{text}
"""

_GENERATE_PROMPT_EN = """\
The following text is from educational content in English.

Task: Generate exactly {count} multiple-choice questions based on the information in the text.

Rules:
1. Each question must test genuine understanding, not mere memorization.
2. Four options per question — one clearly correct, three plausible but wrong.
3. correct_index: 0-based index of the correct option.
4. Include a brief explanation (one sentence) for why the answer is correct.
5. Questions should be clear and academically appropriate.

Required JSON:
{{"questions":[{{"question":"...","options":["...","...","...","..."],"correct_index":0,"explanation":"..."}}]}}

Text:
{text}
"""


def _choose_prompt(mode: str, language: str, count: int, text: str) -> str:
    if language == "arabic":
        template = _EXTRACT_PROMPT_AR if mode == "extract" else _GENERATE_PROMPT_AR
    else:
        template = _EXTRACT_PROMPT_EN if mode == "extract" else _GENERATE_PROMPT_EN
    return template.format(count=count, text=text[:8000])


# ─── الدالة الرئيسية ──────────────────────────────────────────────────────────

async def generate_questions(
    text_content: str,
    count: int = 5,
    language: str = "arabic",
    max_retries: int = 2,
    mode: str | None = None,
) -> list[dict]:
    """
    توليد / استخراج أسئلة اختيار من متعدد.

    Args:
        text_content : النص المصدر
        count        : عدد الأسئلة المطلوب
        language     : "arabic" | "english"
        max_retries  : محاولات إعادة المحاولة عند الفشل
        mode         : "extract" | "generate" | "auto" | None (يقرأ من config)

    Returns:
        قائمة dicts: {question, options, correct_index, explanation?}
        ملاحظة: correct_index محفوظ للمقارنة عند الإجابة فقط،
                ولا يُعرض للمستخدم مباشرة في الواجهة الأمامية.
    """
    if not text_content or not text_content.strip():
        logger.warning("generate_questions: النص فارغ")
        return []

    # تحديد الوضع
    effective_mode = mode or QUIZ_MODE
    if effective_mode == "auto":
        effective_mode = "extract" if _detect_has_mcq(text_content, language) else "generate"
        logger.info("🔍 وضع auto → %s (language=%s)", effective_mode, language)

    model_to_use = OPENAI_MODEL
    prompt = _choose_prompt(effective_mode, language, count, text_content)

    for attempt in range(1, max_retries + 2):
        try:
            response = await _client.chat.completions.create(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.15,
                max_completion_tokens=4096,
            )

            raw = response.choices[0].message.content or ""
            data = json.loads(raw)
            questions = data.get("questions", [])

            # ── المرحلة 1: التحقق البنيوي ───────────────────────────────────
            valid = _validate_questions(questions)

            # إذا كنا في وضع extract وما وجدنا أسئلة → جرّب generate تلقائياً
            if not valid and effective_mode == "extract":
                logger.info("🔄 لا أسئلة جاهزة — جاري التبديل لوضع generate")
                effective_mode = "generate"
                prompt = _choose_prompt(effective_mode, language, count, text_content)
                continue

            # ── المرحلة 2: فحص الجودة ───────────────────────────────────────
            valid, warnings = quality_check(valid)
            if warnings:
                logger.info(
                    "⚠️ استُبعد %d سؤال بعد فحص الجودة — تبقى %d",
                    len(warnings), len(valid),
                )

            logger.info(
                "✅ %d سؤال جاهز للإرسال (طُلب %d، وضع=%s، محاولة=%d)",
                len(valid), count, effective_mode, attempt,
            )

            return valid

        except json.JSONDecodeError as e:
            logger.warning("JSON error (محاولة %d): %s", attempt, e)

        except Exception as e:
            logger.error("OpenAI error (محاولة %d): %s", attempt, e)
            if attempt == 1 and OPENAI_FALLBACK_MODEL:
                logger.info("🔄 تبديل للموديل الاحتياطي: %s", OPENAI_FALLBACK_MODEL)
                model_to_use = OPENAI_FALLBACK_MODEL

    logger.error("❌ فشل توليد الأسئلة بعد %d محاولات", max_retries + 1)
    return []


# ─── التحقق من الأسئلة ───────────────────────────────────────────────────────

# الحد الأدنى لطول السؤال (حرف)
_MIN_QUESTION_LEN = 10

# الحد الأدنى لطول كل خيار — مضبوط على 1 لدعم الخيارات العربية القصيرة
# مثال: "أ" أو "لا" كلاهما يساوي حرفاً أو حرفين وهو صحيح
_MIN_OPTION_LEN = 1

# نسبة التشابه التي نعتبر عندها سؤالَين مكررَين (0-1)
_DUP_SIMILARITY = 0.75


def _normalize(text: str) -> str:
    """
    تطبيع النص للمقارنة الداخلية فقط: أحرف صغيرة، حذف المسافات والتشكيل.
    هذه الدالة لا تُعدّل بيانات الأسئلة الأصلية — تُستخدم فقط داخل
    دوال المقارنة مثل _similarity() وقبل فحص التكرار.
    """
    text = unicodedata.normalize("NFKC", text.lower())
    # حذف التشكيل العربي فقط للمقارنة
    text = re.sub(r"[\u0610-\u061A\u064B-\u065F]", "", text)
    # حذف علامات الترقيم والمسافات الزائدة
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: str, b: str) -> float:
    """
    نسبة التشابه بين نصّين بناءً على الكلمات المشتركة (Jaccard).
    سريعة وكافية لكشف الأسئلة المكررة.
    تعمل على النص المُطبَّع داخلياً فقط — لا تُعيد نتائج مرئية.
    """
    set_a = set(_normalize(a).split())
    set_b = set(_normalize(b).split())
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _validate_questions(questions: list) -> list[dict]:
    """
    المرحلة الأولى: التحقق البنيوي.
    - يتحقق من وجود السؤال والخيارات وصحة correct_index
    - يُصلح الأخطاء الصغيرة تلقائياً (correct_index كنص، خيارات فارغة...)
    - يحفظ النصوص العربية الأصلية دون أي تعديل أو تشويه
    """
    valid: list[dict] = []

    for q in questions:
        if not isinstance(q, dict):
            continue

        # ── استخراج البيانات الخام كما هي من الـ JSON ──────────────────────
        question_text = (q.get("question") or "").strip()
        options = q.get("options", [])
        correct_index = q.get("correct_index", 0)

        # ── فحص السؤال ──────────────────────────────────────────────────────
        if len(question_text) < _MIN_QUESTION_LEN:
            logger.debug("تجاهل سؤال قصير جداً: %r", question_text[:30])
            continue

        # ── فحص الخيارات ────────────────────────────────────────────────────
        if not isinstance(options, list) or len(options) < 2:
            logger.debug("تجاهل سؤال بخيارات غير كافية")
            continue

        # تنظيف الخيارات: إزالة المسافات الزائدة فقط — النص يبقى كما هو
        cleaned_options = [str(o).strip() for o in options if str(o).strip()]

        # حذف الخيارات الفارغة تماماً (الأقل من _MIN_OPTION_LEN حرف)
        cleaned_options = [o for o in cleaned_options if len(o) >= _MIN_OPTION_LEN]

        if len(cleaned_options) < 2:
            logger.debug("تجاهل سؤال بعد حذف الخيارات الفارغة")
            continue

        # ── تصحيح correct_index ──────────────────────────────────────────────
        try:
            correct_index = int(correct_index)
        except (ValueError, TypeError):
            correct_index = 0
        correct_index = max(0, min(correct_index, len(cleaned_options) - 1))

        # ── بناء السؤال النهائي بالنصوص الأصلية ────────────────────────────
        item: dict = {
            "question": question_text,       # النص العربي الأصلي غير مُعدَّل
            "options": cleaned_options,       # الخيارات الأصلية غير مُعدَّلة
            "correct_index": correct_index,   # رقم فقط، لا يُعرض في الواجهة مباشرة
        }

        # explanation اختيارية
        explanation = (q.get("explanation") or "").strip()
        if explanation:
            item["explanation"] = explanation

        valid.append(item)

    return valid


def quality_check(questions: list[dict]) -> tuple[list[dict], list[str]]:
    """
    المرحلة الثانية: فحص الجودة بعد التحقق البنيوي.
    تُعيد: (الأسئلة_الجيدة, قائمة_التحذيرات)

    الفحوصات:
    1. خيارات مكررة داخل نفس السؤال
    2. الإجابة الصحيحة فارغة أو مفقودة
    3. السؤال والإجابة الصحيحة متطابقان (hallucination واضح)
    4. أسئلة مكررة أو شبه مكررة (Jaccard similarity)

    ملاحظة مهمة: هذه الدالة تستخدم _normalize() و _similarity() للمقارنة
    الداخلية فقط. النصوص الأصلية (العربية أو الإنجليزية) تُعاد كما هي
    في قائمة passed دون أي تعديل.
    """
    passed: list[dict] = []
    warnings: list[str] = []
    seen_questions: list[str] = []  # لكشف التكرار (نصوص أصلية للمقارنة)

    for i, q in enumerate(questions, 1):
        q_text = q["question"]
        options = q["options"]
        correct_i = q["correct_index"]
        correct_t = options[correct_i] if correct_i < len(options) else ""
        issues: list[str] = []

        # ── 1. خيارات مكررة داخل السؤال ─────────────────────────────────────
        # المقارنة تتم على النص المُطبَّع، لكن الخيارات المُعادة أصلية
        norm_opts = [_normalize(o) for o in options]
        if len(norm_opts) != len(set(norm_opts)):
            issues.append("خيارات مكررة")

        # ── 2. الإجابة الصحيحة فارغة ─────────────────────────────────────────
        if not correct_t.strip():
            issues.append("الإجابة الصحيحة فارغة")

        # ── 3. السؤال متطابق مع الإجابة (hallucination) ──────────────────────
        # _similarity() تعمل على نسخ مُطبَّعة داخلياً فقط
        if _similarity(q_text, correct_t) > 0.9:
            issues.append("السؤال والإجابة متطابقان تقريباً")

        # ── 4. تكرار مع سؤال سابق ────────────────────────────────────────────
        for seen in seen_questions:
            if _similarity(q_text, seen) >= _DUP_SIMILARITY:
                issues.append("مكرر مع سؤال آخر")
                break

        if issues:
            msg = f"سؤال {i} استُبعد ({' | '.join(issues)}): {q_text[:50]}…"
            warnings.append(msg)
            logger.warning("🔍 %s", msg)
            continue

        # ── السؤال اجتاز الفحص → يُضاف كما هو بدون أي تعديل ────────────────
        seen_questions.append(q_text)
        passed.append(q)  # q يحتوي على النصوص الأصلية غير المُعدَّلة

    if warnings:
        logger.info(
            "✅ جودة الأسئلة: %d مقبول، %d مستبعد",
            len(passed), len(warnings),
        )

    return passed, warnings
