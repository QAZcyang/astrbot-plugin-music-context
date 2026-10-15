"""Conservative Chinese translation admission, not general language detection.

The NetEase field is only a candidate. Shared Han-only vocabulary is ambiguous;
require Chinese grammatical evidence as well as source and script checks. False
negatives are intentional. This is an explainable heuristic, not a language or
translation-quality proof, and never rewrites the stored source text.
"""

import re
import unicodedata

from ..models.lyrics import MusicLyrics
from .cleaner import clean_lyrics

_OTHER_SCRIPTS = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\u0400-\u052f\u0600-\u06ff]")
_HAN = re.compile(r"[\u3400-\u9fff]")
# Multi-character Chinese constructions; single Han characters are never proof.
_MARKERS = (
    "我们", "你们", "他们", "她们", "什么", "为什么", "怎么", "这样", "那么",
    "没有", "不要", "已经", "还是", "只是", "让我", "让你", "你的", "我的",
    "我会", "不会", "我想", "你是", "我是", "一起", "不再", "为了", "着你",
    "我們", "你們", "他們", "什麼", "為什麼", "怎麼", "這樣", "那麼",
    "沒有", "已經", "還是", "讓我", "讓你", "會不會", "為了",
)
_CREDITS = re.compile(r"^(?:作词|作曲|编曲|作詞|編曲|翻译|翻譯|译者|翻訳|制作人|製作人)\s*[:：]")
_PLACEHOLDERS = {"暂无歌词", "暂无翻译", "纯音乐，请欣赏", "純音樂，請欣賞", "无", "無", "暂无译文"}


def chinese_translation(lyrics: MusicLyrics) -> tuple[str, str | None]:
    """Return usable cleaned text or a stable fallback reason (no raw logging)."""
    if not lyrics.translation:
        return "", "translation_missing"
    text = clean_lyrics(lyrics.translation, max_chars=len(lyrics.translation)).text
    lines = [line for line in text.splitlines() if not _CREDITS.match(line)]
    text = "\n".join(line for line in lines if line not in _PLACEHOLDERS)
    if not text:
        return "", "translation_unusable"
    if lyrics.source != "netease" or lyrics.translation_language not in (None, "zh", "zh-CN", "zh-Hans", "zh-Hant", "zh-TW"):
        return "", "translation_language_unconfirmed"
    han = len(_HAN.findall(text))
    letters = [char for char in text if char.isalpha()]
    latin = sum("LATIN" in unicodedata.name(char, "") for char in letters)
    other_letters = any(
        not _HAN.fullmatch(char) and "LATIN" not in unicodedata.name(char, "")
        for char in letters
    )
    if _OTHER_SCRIPTS.search(text) or other_letters or han < 4 or latin > han:
        return "", "translation_language_unconfirmed"
    # Even an explicit label cannot turn shared Japanese kanji into proven Chinese.
    if sum(marker in text for marker in _MARKERS) < 2:
        return "", "translation_language_unconfirmed"
    return text, None
