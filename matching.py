"""Flexible keyword matching for voice transcripts (mixed Bangla/English, dialects, slurred speech).

Text posts still use the strict whole-word matcher in bot.py. Voice transcripts are messier, so
here the transcript and keyword are first normalized (case-folded, Latin accents removed,
punctuation dropped) and then compared with fuzzy similarity over word windows.

Uses rapidfuzz when installed (fast); falls back to Python's difflib otherwise.
"""
import os
import re
import unicodedata

try:
    from rapidfuzz import fuzz as _fuzz

    def _ratio(a: str, b: str) -> float:
        return _fuzz.ratio(a, b)
except Exception:  # library missing -> slower stdlib fallback, same scale (0-100)
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100


def _threshold() -> int:
    try:
        return max(50, min(100, int(os.getenv("VOICE_FUZZY_THRESHOLD", "85"))))
    except ValueError:
        return 85


THRESHOLD = _threshold()
# Keywords shorter than this (after normalizing) must match a whole word exactly —
# fuzzy-matching 2-3 letter words would fire on almost anything.
MIN_FUZZY_LEN = 4

# Bangla letters people/Whisper use interchangeably (spelling + dialect variants) -> one form.
_BN_FOLD = (
    ("\u09c0", "\u09bf"), ("\u09c2", "\u09c1"),  # ী->ি  ূ->ু
    ("\u0988", "\u0987"), ("\u098a", "\u0989"),  # ঈ->ই  ঊ->উ
    ("\u09a3", "\u09a8"),                          # ণ->ন
    ("\u09b7", "\u09b8"), ("\u09b6", "\u09b8"),  # ষ,শ->স
    ("\u09ce", "\u09a4"),                          # ৎ->ত
    ("\u09a1\u09bc", "\u09b0"), ("\u09dc", "\u09b0"),  # ড়->র
    ("\u09a2\u09bc", "\u09b0"), ("\u09dd", "\u09b0"),  # ঢ়->র
)
_NON_WORD = re.compile(r"[^\w\u0300-\u036F\u0900-\u0DFF\u200C\u200D]+")


def normalize(text: str) -> str:
    """Lowercase, strip accents on Latin letters (é -> e), drop punctuation/emoji, collapse spaces.
    Bangla/Indic vowel signs are kept — removing them would change the word."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.casefold())
    out, prev_latin = [], False
    for ch in text:
        if unicodedata.category(ch) == "Mn" and prev_latin:
            continue  # accent on a Latin letter
        prev_latin = ch.isascii() and ch.isalpha()
        out.append(ch)
    text = unicodedata.normalize("NFC", "".join(out)).replace("\u200c", "").replace("\u200d", "")
    for a, b in _BN_FOLD:
        text = text.replace(a, b)
    return _NON_WORD.sub(" ", text).strip()


def fuzzy_match(word: str, text: str, threshold: int = None) -> bool:
    """True if `word` (single word or phrase) appears in `text`, tolerating small spelling/
    pronunciation differences, case, accents and punctuation."""
    threshold = THRESHOLD if threshold is None else threshold
    kw = normalize(word)
    body = normalize(text)
    if not kw or not body:
        return False
    tokens = body.split()
    kw_tokens = kw.split()
    if f" {kw} " in f" {body} ":  # exact after normalizing
        return True
    if len(kw.replace(" ", "")) < MIN_FUZZY_LEN:
        return False
    n = len(kw_tokens)
    kw_joined = kw.replace(" ", "")
    # Compare against windows of n-1..n+1 words so "ফোন করো" ~ "ফোনকরো" and split/merged words match.
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(1, len(tokens) - size + 1)):
            window = tokens[i:i + size]
            if not window:
                continue
            cand = " ".join(window)
            if _ratio(kw, cand) >= threshold or _ratio(kw_joined, cand.replace(" ", "")) >= threshold:
                return True
    return False
