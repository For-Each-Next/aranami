"""Count words using language-specific tokenization.

Call ``count_words`` on extracted plain text. Chinese segmentation uses
jieba precise mode with its HMM enabled and keeps disposable dictionary
files beneath the caller's ``cache/words/`` directory.
"""

from __future__ import annotations

import re
import unicodedata
from functools import cache
from pathlib import Path

import jieba

_ENGLISH_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
_COUNTABLE_TOKEN_RE = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFFA-Za-z0-9]",
)
_CJK_CHARACTER_RE = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
    r"\u3040-\u30FF\uAC00-\uD7AF\U00020000-\U000323AF]",
)


def count_chinese_units(text: str) -> int:
    """Count CJK characters and double-weight Western words and numbers.

    Args:
        text: Extracted plain prose, without source markup.

    Returns:
        One unit per CJK letter and two per consecutive Western-letter
        word or decimal-digit block. Spaces and punctuation contribute
        zero. Combining accents remain part of their preceding word.
    """
    total = 0
    previous = None
    for character in text:
        if character.isalpha() and _CJK_CHARACTER_RE.fullmatch(character):
            total += 1
            previous = None
        elif character.isalpha():
            total += 2 if previous != "word" else 0
            previous = "word"
        elif character.isdecimal():
            total += 2 if previous != "number" else 0
            previous = "number"
        elif unicodedata.category(character).startswith("M"):
            continue
        else:
            previous = None
    return total


@cache
def _tokenizer(directory: Path) -> jieba.Tokenizer:
    """Reuse a tokenizer whose disposable dictionary cache stays local.

    Args:
        directory: Caller-relative runtime cache directory.

    Returns:
        A precise-mode tokenizer with a cache outside the uploaded
        source tree.
    """
    directory.mkdir(parents=True, exist_ok=True)
    tokenizer = jieba.Tokenizer()
    tokenizer.tmp_dir = str(directory)
    return tokenizer


def count_words(text: str, *, language: str) -> int:
    """Count English words or Chinese segmented tokens in plain text.

    English counts alphanumeric words with internal apostrophes and
    hyphens. Chinese counts jieba tokens containing Chinese characters
    or alphanumeric text and excludes punctuation and whitespace.

    Args:
        text: Plain text after any caller-specific prose extraction.
        language: Language code, currently en or zh.

    Returns:
        Number of countable words or tokens.

    Raises:
        ValueError: If the requested language is unsupported.
    """
    if language == "en":
        return len(_ENGLISH_WORD_RE.findall(text))
    if language == "zh":
        tokenizer = _tokenizer(Path.cwd() / "cache" / "words")
        return sum(
            1
            for token in tokenizer.cut(text, cut_all=False, HMM=True)
            if _COUNTABLE_TOKEN_RE.search(token)
        )
    message = f"Unsupported word-count language: {language}."
    raise ValueError(message)
