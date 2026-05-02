"""
services/pdf_service.py — استخراج النص من ملفات PDF

إصلاحات:
  - logging بدلاً من print
  - معالجة كل صفحة باستقلالية (خطأ في صفحة لا يوقف الكل)
  - دعم الـ PDF المحمية بكلمة مرور برسالة واضحة
  - إحصائيات الاستخراج (عدد الصفحات، الحروف)
  - تنظيف أفضل للنص (أحرف غير مطبوعة، فراغات زائدة)
"""
import io
import logging

import pdfplumber

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    استخراج النص من ملف PDF.

    Args:
        file_bytes: محتوى الملف كـ bytes

    Returns:
        النص المستخرج، أو سلسلة فارغة عند الفشل
    """
    if not file_bytes:
        logger.warning("extract_text_from_pdf: file_bytes فارغ")
        return ""

    text_parts: list[str] = []
    total_pages   = 0
    failed_pages  = 0

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total_pages = len(pdf.pages)

            if total_pages == 0:
                logger.warning("PDF فارغ — لا توجد صفحات")
                return ""

            logger.info("📄 بدء استخراج النص من %d صفحة", total_pages)

            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        cleaned = _clean_page_text(page_text)
                        if cleaned:
                            text_parts.append(cleaned)
                    else:
                        logger.debug("صفحة %d: لا يوجد نص قابل للاستخراج", page_num)
                except Exception as page_err:
                    failed_pages += 1
                    logger.warning("خطأ في صفحة %d: %s", page_num, page_err)

    except pdfplumber.utils.exceptions.PDFSyntaxError as e:
        logger.error("PDF تالف أو غير صالح: %s", e)
        return ""
    except Exception as e:
        # بعض PDFs المحمية تُطلق exceptions عامة
        err_msg = str(e).lower()
        if "password" in err_msg or "encrypted" in err_msg:
            logger.error("PDF محمي بكلمة مرور — لا يمكن الاستخراج")
        else:
            logger.error("خطأ غير متوقع في استخراج PDF: %s", e, exc_info=True)
        return ""

    if failed_pages > 0:
        logger.warning(
            "⚠️ فشل استخراج %d/%d صفحة", failed_pages, total_pages
        )

    full_text = "\n\n".join(text_parts).strip()
    logger.info(
        "✅ تم الاستخراج: %d/%d صفحة — %d حرف",
        total_pages - failed_pages, total_pages, len(full_text),
    )

    return full_text


# ─── Helper ───────────────────────────────────────────────────────────────────

def _clean_page_text(text: str) -> str:
    """
    تنظيف نص الصفحة:
      - إزالة الأحرف غير المطبوعة (ما عدا newline وtab)
      - ضغط الفراغات المتكررة
      - إزالة الأسطر الفارغة الزائدة
    """
    import re

    # إزالة الأحرف غير المطبوعة (control chars ما عدا \n \t)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    # ضغط الفراغات الأفقية
    text = re.sub(r"[ \t]+", " ", text)
    # ضغط الأسطر الفارغة (أكثر من سطرين متتاليين → سطر واحد)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
