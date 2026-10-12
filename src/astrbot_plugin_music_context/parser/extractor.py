"""Extract URL candidates without assuming every message is plain text."""

import re
from collections.abc import Iterable
from typing import Protocol

from .result import MusicLinkCandidate

_URL_PATTERN = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9_@/.:\\-])[a-z][a-z0-9+.-]*://"
    r"|(?<![\w@/.:\\-])(?:[a-z0-9-]+\.)+[a-z0-9-]+/"
    r")"
    r"[^\s<>\"'`，。；：！？、「」『』《》]+",
    re.IGNORECASE,
)
_CLOSING_BRACKETS = {")": "(", "]": "[", "}": "{"}


class MessageExtractor(Protocol):
    """Boundary for future message-chain or structured-message adapters."""

    def extract_urls(self, message: object) -> Iterable[str]:
        """Yield URL candidates in message order without fetching their content."""
        ...


class TextURLExtractor:
    """Extract text URLs; unsupported message objects are ignored, not stringified."""

    def extract_urls(self, message: object) -> Iterable[str]:
        """Compatibility projection for existing MusicParser integrations."""
        for candidate in self.extract_candidates(message):
            yield candidate.url

    def extract_candidates(self, message: object) -> Iterable[MusicLinkCandidate]:
        """Yield text-origin candidates without interpreting song metadata."""
        if not isinstance(message, str):
            return

        for match in _URL_PATTERN.finditer(message):
            url = match.group().rstrip(".,;:!?")
            # Remove wrapper brackets, but preserve balanced brackets in the URL.
            while url and url[-1] in _CLOSING_BRACKETS:
                closing = url[-1]
                if url.count(closing) <= url.count(_CLOSING_BRACKETS[closing]):
                    break
                url = url[:-1].rstrip(".,;:!?")
            yield MusicLinkCandidate(url=url, source="text")
