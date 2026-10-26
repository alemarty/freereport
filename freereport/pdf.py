"""PDF reading and quote verification.

Claude reads the PDF directly (it sees equations and tables as rendered).
The extracted text here is used to check that each quoted passage really
exists in the paper, and later as the input for open models on HPC.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf


def page_texts(pdf_path: Path) -> list[str]:
    with pymupdf.open(pdf_path) as doc:
        return [page.get_text() for page in doc]


_REPLACEMENTS = {"−": "-", "–": "-", "—": "-", "ﬁ": "fi", "ﬂ": "fl", " ": " "}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    text = re.sub(r"-\s*\n\s*", "", text)  # re-join hyphenated line breaks
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_match(quote: str, pages: list[str], page: int) -> float:
    """Return how much of the quote (0 to 1) is found in the paper.

    Looks on the cited page and its neighbours first, then the whole paper.
    Equations often extract as garbled text, so a low score means
    "could not confirm", not "invented".
    """
    q = normalise(quote)
    if not q:
        return 0.0
    nearby = [p for p in (page - 2, page - 1, page) if 0 <= p < len(pages)]
    candidates = [normalise(pages[i]) for i in nearby] + [normalise(" ".join(pages))]
    best = 0.0
    for text in candidates:
        if q in text:
            return 1.0
        m = SequenceMatcher(None, text, q, autojunk=False).find_longest_match(0, len(text), 0, len(q))
        best = max(best, m.size / len(q))
    return best
