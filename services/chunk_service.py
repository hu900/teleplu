"""
services/chunk_service.py — تقطيع النص إلى chunks ذكية

إصلاحات:
  - استخدام CHUNK_MIN_CHARS من config في فلترة النهاية (كانت 120 ثابتة)
  - logging بدلاً من print
  - معالجة edge case: نص أقصر من min_chars يُعطى chunk واحداً
  - prepare_chunks تُرجع قائمة فارغة برسالة تحذير إذا كان النص قصيراً جداً
"""
import hashlib
import logging
import re
from collections import Counter

from config import CHUNK_MAX_CHARS, CHUNK_MIN_CHARS, CHUNK_TARGET_CHARS

logger = logging.getLogger(__name__)

# ─── Stop Words ───────────────────────────────────────────────────────────────

AR_STOPWORDS = {
    "في", "من", "على", "إلى", "عن", "هذا", "هذه", "ذلك", "تلك", "كان", "كانت",
    "هو", "هي", "ثم", "كما", "أو", "و", "ف", "ب", "ل", "أن", "إن", "قد", "تم",
    "ما", "مع", "بعد", "قبل", "بين", "إذا", "كل", "أي", "أحد", "هناك", "هنا",
    "عند", "ضمن", "حول", "حتى", "لم", "لن", "لا", "غير", "بعض", "أكثر", "أقل",
    "يمكن", "يجب", "يعد", "يُعد", "حيث", "التي", "الذي", "الذين", "اللواتي",
}

EN_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "from", "with",
    "by", "at", "as", "is", "are", "was", "were", "be", "been", "being", "that",
    "this", "these", "those", "it", "its", "if", "then", "than", "into", "about",
    "over", "under", "between", "during", "before", "after", "such", "can", "could",
    "may", "might", "will", "would", "should", "not", "no", "yes", "more", "most",
    "also", "just", "only", "some", "any", "all", "each", "both", "few", "own",
}


# ─── تطبيع النص ───────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """تنظيف وتوحيد تنسيق النص."""
    text = text.replace("\x00", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─── تقسيم إلى جمل ───────────────────────────────────────────────────────────

def split_into_sentences(text: str) -> list[str]:
    """تقسيم النص إلى جمل باستخدام علامات الترقيم وفواصل الأسطر."""
    text = normalize_text(text)
    # فصل عند نقطة/علامة استفهام/تعجب + مسافة أو سطر جديد
    parts = re.split(r'(?<=[\.\!\?؟])\s+|\n+', text)
    return [p.strip() for p in parts if p.strip()]


# ─── بناء الـ Chunks ──────────────────────────────────────────────────────────

def build_chunks(
    text: str,
    target_chars: int = CHUNK_TARGET_CHARS,
    min_chars: int    = CHUNK_MIN_CHARS,
    max_chars: int    = CHUNK_MAX_CHARS,
) -> list[str]:
    """
    تقطيع النص إلى chunks متوازنة الحجم.

    - يحاول الحفاظ على الجمل كاملة
    - يكسر الجمل الطويلة جداً عند الفواصل
    - يدمج الـ chunk الأخير مع السابق إذا كان صغيراً جداً
    """
    sentences = split_into_sentences(text)

    if not sentences:
        text = normalize_text(text)
        return [text] if text else []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # كسر الجمل الطويلة جداً عند الفواصل
        parts = [sentence]
        if len(sentence) > max_chars:
            parts = [p.strip() for p in re.split(r'[،,;؛]\s*', sentence) if p.strip()]

        for part in parts:
            part_len = len(part) + 1
            if current and (current_len + part_len > target_chars) and current_len >= min_chars:
                chunks.append(" ".join(current).strip())
                current     = [part]
                current_len = len(part)
            else:
                current.append(part)
                current_len += part_len

    # معالجة ما تبقى
    if current:
        final_chunk = " ".join(current).strip()
        if chunks and len(final_chunk) < min_chars:
            # دمج مع الـ chunk الأخير
            chunks[-1] = f"{chunks[-1]} {final_chunk}".strip()
        else:
            chunks.append(final_chunk)

    # فلترة باستخدام CHUNK_MIN_CHARS من config (كانت 120 ثابتة خطأً)
    valid_chunks = [c for c in chunks if len(c) >= min_chars]

    # إذا لم يبقَ شيء (نص قصير)، أرجع الـ chunk الأكبر بدون فلترة
    if not valid_chunks and chunks:
        valid_chunks = [max(chunks, key=len)]

    return valid_chunks


# ─── Keywords ─────────────────────────────────────────────────────────────────

def _extract_words(text: str, language: str) -> list[str]:
    if language == "arabic":
        return re.findall(r'[\u0600-\u06FF]{3,}', text)
    return re.findall(r'[A-Za-z]{3,}', text.lower())


def extract_keywords(text: str, language: str, top_n: int = 8) -> list[str]:
    words     = _extract_words(text, language)
    stopwords = AR_STOPWORDS if language == "arabic" else EN_STOPWORDS
    words     = [w for w in words if w.lower() not in stopwords]
    freq      = Counter(words)
    return [word for word, _ in freq.most_common(top_n)]


# ─── Learning Points ──────────────────────────────────────────────────────────

def extract_learning_points(text: str, language: str, max_points: int = 4) -> list[str]:
    """استخراج أبرز الجمل كنقاط تعلم."""
    sentences = split_into_sentences(text)
    points: list[str] = []
    seen: set[str] = set()

    for s in sentences:
        s = s.strip()
        if len(s) < 40:
            continue
        s_key = s.lower()
        if s_key in seen:
            continue
        seen.add(s_key)
        points.append(s)
        if len(points) >= max_points:
            break

    return points


# ─── الدالة الرئيسية ──────────────────────────────────────────────────────────

def prepare_chunks(file_hash: str, text: str, language: str) -> list[dict]:
    """
    تجهيز الـ chunks الكاملة للحفظ في قاعدة البيانات.

    Returns:
        قائمة من dicts تحتوي على:
        chunk_index, chunk_hash, chunk_text, keywords, learning_points
    """
    text = normalize_text(text)

    if not text:
        logger.warning("prepare_chunks: النص فارغ لـ file_hash=%s", file_hash[:8])
        return []

    raw_chunks = build_chunks(text)

    if not raw_chunks:
        logger.warning(
            "prepare_chunks: لم ينتج أي chunk من النص (طول=%d)", len(text)
        )
        return []

    result: list[dict] = []
    for idx, chunk_text in enumerate(raw_chunks):
        chunk_hash = hashlib.sha256(
            f"{file_hash}:{idx}:{chunk_text}".encode("utf-8")
        ).hexdigest()

        result.append({
            "chunk_index":     idx,
            "chunk_hash":      chunk_hash,
            "chunk_text":      chunk_text,
            "keywords":        extract_keywords(chunk_text, language),
            "learning_points": extract_learning_points(chunk_text, language),
        })

    logger.info(
        "✅ prepare_chunks: %d chunk من %d حرف (file=%s)",
        len(result), len(text), file_hash[:8],
    )
    return result
