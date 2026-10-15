"""Pivot mode: Bhashini NMT translates her question to English and the answer back.

Amounts, numbers and names are swapped for placeholder tokens before translation and put back after, so
NMT cannot garble ₹1,24,800 or "Sunita". If a token does not survive, the text is translated without them.
"""
import re

from bhashini import client as bhashini

_AMOUNT = re.compile(r'₹\s?[\d,]+(?:\.\d+)?|\d[\d,]*(?:\.\d+)?')


def _token(i):
    """ZQAZ, ZQBZ, … : letters only, so the amount pattern can never match inside a token."""
    letters = ''
    while True:
        letters = chr(65 + i % 26) + letters
        i = i // 26 - 1
        if i < 0:
            return f'ZQ{letters}Z'


def _protect(text, names):
    kept = []

    def keep(value):
        kept.append(value)
        return f' {_token(len(kept) - 1)} '

    text = _AMOUNT.sub(lambda m: keep(m.group(0)), text)  # amounts first: names never contain digits
    for name in sorted({n for n in names if n and len(n) > 1}, key=len, reverse=True):
        if name in text:
            text = text.replace(name, keep(name))
    return text, kept


def _restore(text, kept):
    for i, value in enumerate(kept):
        token = re.compile(rf'\s*{_token(i)}\s*', re.I)
        if not token.search(text):
            return None
        text = token.sub(f' {value} ', text, count=1)
    return re.sub(r'\s+([।,.!?:])', r'\1', re.sub(r'\s{2,}', ' ', text)).strip()


def translate(text, source, target, names=()):
    if source == target or not text.strip():
        return text
    protected, kept = _protect(text, names)
    out = bhashini.translate(protected, source, target)['text']
    restored = _restore(out, kept)
    return restored if restored is not None else bhashini.translate(text, source, target)['text']
