import os

# ─── Telegram ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# معرّفات المشرفين (مفصولة بفواصل) — اختياري
BOT_ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("BOT_ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# ─── OpenAI ──────────────────────────────────────────────────────────────────
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL       = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", "")

# ─── Database ────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/data/quiz_bot.db")

# ─── PDF Settings ────────────────────────────────────────────────────────────
MAX_PDF_SIZE_MB      = int(os.getenv("MAX_PDF_SIZE_MB",      "10"))
MAX_CHUNK_TEXT_CHARS = int(os.getenv("MAX_CHUNK_TEXT_CHARS", "1400"))
CHUNK_TARGET_CHARS   = int(os.getenv("CHUNK_TARGET_CHARS",   "1200"))
CHUNK_MIN_CHARS      = int(os.getenv("CHUNK_MIN_CHARS",      "500"))
CHUNK_MAX_CHARS      = int(os.getenv("CHUNK_MAX_CHARS",      "1600"))

# ─── Quiz Settings ───────────────────────────────────────────────────────────
MAX_RESULTS_DISPLAY    = int(os.getenv("MAX_RESULTS_DISPLAY",    "20"))
ANSWER_TIMEOUT_SECONDS = int(os.getenv("ANSWER_TIMEOUT_SECONDS", "0"))

# عدد الأخطاء المعروضة في ملخص نهاية الاختبار
MAX_WRONG_SHOWN = int(os.getenv("MAX_WRONG_SHOWN", "5"))

# وضع توليد الأسئلة:
#   "extract" = استخراج أسئلة موجودة فعلاً في الـ PDF
#   "generate" = توليد أسئلة جديدة من المحتوى التعليمي
#   "auto"    = الكشف التلقائي (افتراضي)
QUIZ_MODE = os.getenv("QUIZ_MODE", "auto")
