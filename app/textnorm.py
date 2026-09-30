"""Text normalisation for the enhanced (Arabic + English) search."""
import re
import unicodedata

_MAP = str.maketrans({"ى": "ي", "ة": "ه", "ـ": "", "ک": "ك", "ی": "ي"})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize(s):
    """Lowercase, strip accents/diacritics/tatweel, unify Arabic letter variants and digits.

    أ إ آ -> ا, ؤ -> و, ئ -> ي (via Unicode decomposition), ى -> ي, ة -> ه, Arabic-Indic digits -> ASCII.
    """
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.translate(_MAP).translate(_DIGITS)


def tokens(q):
    return [t for t in normalize(q).split() if t]


def build_search_text(title):
    parts = [title.title, title.title_alt, title.author, title.publisher, title.category,
             title.call_number, title.isbn or "", title.pub_year]
    return " ".join(normalize(p) for p in parts if p)


# ---- ISBN helpers ----
def clean_isbn(raw):
    return re.sub(r"[^0-9Xx]", "", raw or "").upper()


def isbn_valid(i):
    if len(i) == 13 and i.isdigit():
        s = sum(int(c) * (1 if k % 2 == 0 else 3) for k, c in enumerate(i[:12]))
        return (10 - s % 10) % 10 == int(i[12])
    if len(i) == 10 and re.fullmatch(r"\d{9}[\dX]", i):
        return sum((10 - k) * (10 if c == "X" else int(c)) for k, c in enumerate(i)) % 11 == 0
    return False


def isbn10_to_13(i):
    core = "978" + i[:9]
    s = sum(int(c) * (1 if k % 2 == 0 else 3) for k, c in enumerate(core))
    return core + str((10 - s % 10) % 10)


def isbn13_to_10(i):
    if not (len(i) == 13 and i.startswith("978")):
        return None
    core = i[3:12]
    r = (11 - sum((10 - k) * int(c) for k, c in enumerate(core)) % 11) % 11
    return core + ("X" if r == 10 else str(r))


def isbn_variants(raw):
    """All spellings (ISBN-10 and ISBN-13) of a valid ISBN, else []."""
    i = clean_isbn(raw)
    if not isbn_valid(i):
        return []
    if len(i) == 10:
        return [i, isbn10_to_13(i)]
    other = isbn13_to_10(i)
    return [i] + ([other] if other else [])
