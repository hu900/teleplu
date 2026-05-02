"""
services/quiz_service.py — توليد الأسئلة عبر OpenAI

تحسينات v2:
  - وضع "auto": الكشف التلقائي إذا كان PDF يحتوي أسئلة جاهزة أو محتوى تعليمي
  - وضع "extract": استخراج الأسئلة الموجودة فعلاً (للاختبارات السابقة)
  - وضع "generate": توليد أسئلة جديدة من المحتوى التعليمي
  - Fallback تلقائي: إذا فشل extract يجرّب generate
  - برومبت ثنائي اللغة (عربي/إنجليزي) حسب لغة المحتوى
  - دعم حقل explanation اختياري لتفسير الإجابة الصحيحة
"""
import json
import logging
import re

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
    prompt       = _choose_prompt(effective_mode, language, count, text_content)

    for attempt in range(1, max_retries + 2):
        try:
            response = await _client.chat.completions.create(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.15,
                max_completion_tokens=4096,
            )

            raw  = response.choices[0].message.content or ""
            data = json.loads(raw)
            questions = data.get("questions", [])

            valid = _validate_questions(questions)

            # إذا كنا في وضع extract وما وجدنا أسئلة → جرّب generate تلقائياً
            if not valid and effective_mode == "extract":
                logger.info("🔄 لا أسئلة جاهزة — جاري التبديل لوضع generate")
                effective_mode = "generate"
                prompt = _choose_prompt(effective_mode, language, count, text_content)
                continue

            logger.info(
                "✅ %d سؤال (طُلب %d، وضع=%s، محاولة=%d)",
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

def _validate_questions(questions: list) -> list[dict]:
    valid = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        question_text = (q.get("question") or "").strip()
        options       = q.get("options", [])
        correct_index = q.get("correct_index", 0)

        if not question_text:
            continue
        if not isinstance(options, list) or len(options) < 2:
            continue

        # تحويل correct_index للرقم إذا جاء كنص
        try:
            correct_index = int(correct_index)
        except (ValueError, TypeError):
            correct_index = 0
        correct_index = max(0, min(correct_index, len(options) - 1))

        item: dict = {
            "question":      question_text,
            "options":       [str(o).strip() for o in options],
            "correct_index": correct_index,
        }
        # explanation اختيارية
        explanation = (q.get("explanation") or "").strip()
        if explanation:
            item["explanation"] = explanation

        valid.append(item)
    return valid
