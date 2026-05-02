import re

ARABIC_RE = re.compile(r'[\u0600-\u06FF]')
ENGLISH_RE = re.compile(r'[A-Za-z]')

def detect_text_language(text: str) -> str:
    if not text:
        return "arabic"

    arabic_count = len(ARABIC_RE.findall(text))
    english_count = len(ENGLISH_RE.findall(text))

    if arabic_count > english_count:
        return "arabic"
    return "english"

def get_language_label(language: str) -> str:
    return "العربية" if language == "arabic" else "English"
