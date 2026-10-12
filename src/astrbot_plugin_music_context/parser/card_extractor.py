"""Local, dependency-free extraction from text and explicit card layouts."""

import json
import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .extractor import TextURLExtractor
from .result import MusicLinkCandidate

MAX_CARD_CHARS = 65536
MAX_MESSAGE_PARTS = 256
_MAX_URL_CHARS = 2048
_MAX_HINT_CHARS = 512
_XML_START = re.compile(r"<(?:[!?]|[A-Za-z_][\w:.-]*(?:\s|/?>))")


def _hint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(value) <= _MAX_HINT_CHARS else None


def _candidate(
    url: object, source: str, title: object = None, artist: object = None
) -> MusicLinkCandidate | None:
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > _MAX_URL_CHARS:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        return None
    if "\\" in url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        _ = parsed.port  # Validate malformed ports without choosing a platform.
    except ValueError:
        return None
    return MusicLinkCandidate(url, source, _hint(title), _hint(artist))


class CardMessageExtractor:
    """Accept text, QQ JSON payloads, and OneBot segment dictionaries/lists.

    Only known navigation fields are read, never arbitrary nested URLs, images,
    audio streams, replies, or forwarded chains. Unknown layouts yield no data.
    No I/O, redirects, platform ID derivation, or user-intent interpretation.
    """

    # JSON/XML 只提供候选导航链接，标题/歌手只是未验证提示；Parser 验证身份，
    # Provider 再给出事实。只读已知卡片布局，避免把封面、音频或引用消息当作歌曲分享。
    def extract_urls(self, message: object) -> Iterable[str]:
        """Retain the existing MessageExtractor protocol for MusicParser."""
        for candidate in self.extract_candidates(message):
            yield candidate.url

    def extract_candidates(self, message: object) -> Iterable[MusicLinkCandidate]:
        if isinstance(message, (list, tuple)):
            for part in message[:MAX_MESSAGE_PARTS]:
                yield from self._extract_part(part)
        else:
            yield from self._extract_part(message)

    def _extract_part(self, part: object) -> Iterable[MusicLinkCandidate]:
        if isinstance(part, str):
            stripped = part.lstrip()
            if stripped.startswith("{"):
                yield from self.extract_json(part)
            elif _XML_START.match(stripped):
                yield from self.extract_xml(part)
            else:
                yield from TextURLExtractor().extract_candidates(part)
        elif isinstance(part, Mapping):
            if "type" not in part:
                yield from self.extract_json(part)
                return
            data = part.get("data")
            if not isinstance(data, Mapping):
                return
            match part.get("type"):
                case "text":
                    text = data.get("text")
                    if isinstance(text, str):
                        yield from self._extract_part(text)
                case "json":
                    yield from self.extract_json(data.get("data"))
                case "xml":
                    yield from self.extract_xml(data.get("data"))

    def extract_json(self, payload: object) -> Iterable[MusicLinkCandidate]:
        """Read QQ meta.music/meta.news navigation links; hints stay unverified."""
        if isinstance(payload, str):
            if len(payload) > MAX_CARD_CHARS:
                return
            try:
                payload = json.loads(payload)
            except (ValueError, RecursionError):
                return
        if not isinstance(payload, Mapping):
            return
        meta = payload.get("meta")
        if not isinstance(meta, Mapping):
            return
        for layout in ("music", "news"):
            data = meta.get(layout)
            if not isinstance(data, Mapping):
                continue
            title = data.get("title")
            artist = data.get("artist")
            # QQ music cards use desc for the performer. News descriptions do
            # not establish an artist, even when their URL is a music link.
            if artist is None and layout == "music":
                artist = data.get("desc")
            seen: set[str] = set()
            for key in ("jumpUrl", "qqdocurl", "url"):
                candidate = _candidate(data.get(key), "qq_json", title, artist)
                if candidate is not None and candidate.url not in seen:
                    seen.add(candidate.url)
                    yield candidate

    def extract_xml(self, payload: object) -> Iterable[MusicLinkCandidate]:
        """Read msg share URLs or appmsg URLs, rejecting DTD/entity declarations."""
        # 消息内容不可信：尺寸限制与实体声明拒绝用于约束解析成本，而非增加平台识别能力。
        if not isinstance(payload, str) or len(payload) > MAX_CARD_CHARS:
            return
        # ElementTree does not fetch external resources; reject declarations
        # as well to prevent entity expansion in untrusted message content.
        upper = payload.upper()
        if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
            return
        try:
            root = ElementTree.fromstring(payload)
        except (ElementTree.ParseError, ValueError, RecursionError):
            return
        if root.tag == "msg" and "url" in root.attrib:
            candidate = _candidate(
                root.get("url"),
                "xml",
                root.findtext("item/title"),
                root.findtext("item/summary") if root.get("serviceID") == "2" else None,
            )
        else:
            if root.tag == "appmsg":
                app = root
            elif root.tag == "msg":
                app = root.find("appmsg")
            else:
                return
            if app is None:
                return
            candidate = _candidate(
                app.findtext("url"),
                "xml",
                app.findtext("title"),
                app.findtext("des") if app.findtext("type") == "3" else None,
            )
        if candidate is not None:
            yield candidate
