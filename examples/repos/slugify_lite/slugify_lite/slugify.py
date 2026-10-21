"""Turn arbitrary text into URL-safe slugs."""
import re
import unicodedata


def slugify(text, sep="-", max_length=None):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", sep, text.lower()).strip(sep)
    if max_length is not None:
        text = text[:max_length]
    return text
