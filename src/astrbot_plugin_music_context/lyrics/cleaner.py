"""Normalize lyric text without interpreting it or changing source models."""

import re
from dataclasses import dataclass


# Only leading LRC timing tags are structural; lyric brackets stay intact.
_TIMESTAMPS = re.compile(r"^(?:\[[0-9]+:[0-5][0-9](?:[.:][0-9]{1,3})?\]\s*)+")
_METADATA = re.compile(
    r"\[(?:ti|ar|al|by|offset|re|ve|length):[^\]]*\]", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class CleanedLyrics:
    """Clean text and whether the character budget omitted complete lines."""

    text: str = ""
    truncated: bool = False


def clean_lyrics(
    text: str | None,
    *,
    max_chars: int = 6000,
    is_instrumental: bool = False,
) -> CleanedLyrics:
    """Remove LRC tags, blank lines, and consecutive duplicate lines.

    Preserve lyric order, Unicode, and nonconsecutive repetitions. Length
    includes newline separators. Keep a prefix of complete lines, stopping at
    the first line that does not fit; an oversized first line yields empty
    text with ``truncated=True``. Zero allows no text; negative limits raise
    ValueError as a configuration error.

    Original and translated text can be cleaned independently with this API;
    no alignment or language inference is attempted. The limit is per text,
    not a token budget for a complete LLM request. Instrumental status must be
    supplied explicitly. Input strings and MusicLyrics models remain intact.
    """
    if max_chars < 0:
        raise ValueError("max_chars must be nonnegative")
    if is_instrumental or not text:
        return CleanedLyrics()

    lines: list[str] = []
    length = 0
    previous: str | None = None
    for raw_line in text.splitlines():
        line = _TIMESTAMPS.sub("", raw_line.strip()).strip()
        if not line or _METADATA.fullmatch(line):
            continue
        if line == previous:
            continue
        extra_length = len(line) + (1 if lines else 0)
        if length + extra_length > max_chars:
            return CleanedLyrics(text="\n".join(lines), truncated=True)
        lines.append(line)
        length += extra_length
        previous = line

    return CleanedLyrics(text="\n".join(lines))
