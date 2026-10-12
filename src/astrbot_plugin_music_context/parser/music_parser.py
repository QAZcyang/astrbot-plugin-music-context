"""Music URL parsing with optional bounded NetEase short-link resolution."""

import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from .extractor import MessageExtractor, TextURLExtractor
from .result import MusicLinkResult
from .short_link_resolver import ShortLinkResolver

URLParser = Callable[[str], MusicLinkResult | None]
_NETEASE_HOSTS = {"music.163.com", "y.music.163.com"}
_NETEASE_SONG_PATHS = {"/song", "/m/song"}
_MAX_URL_LENGTH = 2048


def _parse_netease_url(url: str) -> MusicLinkResult | None:
    """Parse supported NetEase song routes without network or existence checks."""
    try:
        # Adding a scheme is only for parsing; the result preserves the input URL.
        parsed = urlsplit(url if "://" in url else "https://" + url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if parsed.hostname not in _NETEASE_HOSTS:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        default_port = 443 if parsed.scheme == "https" else 80
        if parsed.port is not None and parsed.port != default_port:
            return None

        route = parsed
        if parsed.path in {"", "/"} and parsed.fragment:
            route = urlsplit("/" + parsed.fragment.lstrip("/"))
        if route.path not in _NETEASE_SONG_PATHS:
            return None
        query = parse_qs(route.query, keep_blank_values=True, max_num_fields=64)
    except ValueError:
        return None

    identifiers = query.get("id", [])
    if not identifiers or len(set(identifiers)) != 1:
        return None
    identifier = identifiers[0]
    if re.fullmatch(r"[0-9]+", identifier) is None:
        return None
    track_id = identifier.lstrip("0")
    if not track_id:
        return None
    return MusicLinkResult(platform="netease", track_id=track_id, url=url)


class MusicParser:
    """Extract candidates and return the first supported song link, or None.

    A custom extractor can accept structured messages. Additional platform URL
    parsers can be supplied without changing extraction or the result model.
    Card extraction is available through an injected CardMessageExtractor.
    """

    def __init__(
        self,
        extractor: MessageExtractor | None = None,
        url_parsers: Iterable[URLParser] | None = None,
        resolve_short_links: bool = True,
        short_link_resolver: ShortLinkResolver | None = None,
    ) -> None:
        self._extractor = extractor if extractor is not None else TextURLExtractor()
        self._url_parsers = (
            tuple(url_parsers) if url_parsers is not None else (_parse_netease_url,)
        )
        self._short_link_resolver = (
            (
                short_link_resolver
                if short_link_resolver is not None
                else ShortLinkResolver()
            )
            if resolve_short_links
            else None
        )

    def parse(self, message: object) -> MusicLinkResult | None:
        """Prefer locally parseable songs, then resolve one short link at most.

        Short-link resolution waits at most three seconds per parse invocation.
        parse_url remains local. Preserve the actual shared URL in the result.
        """
        short_url = None
        for url in self._extractor.extract_urls(message):
            result = self.parse_url(url)
            if result is not None:
                return result
            if (
                short_url is None
                and self._short_link_resolver is not None
                and self._short_link_resolver.supports(url)
            ):
                short_url = url
        if short_url is not None and self._short_link_resolver is not None:
            resolved = self._short_link_resolver.resolve(short_url)
            result = self.parse_url(resolved) if resolved is not None else None
            if result is not None:
                return replace(result, url=short_url)
        return None

    def parse_url(self, url: str) -> MusicLinkResult | None:
        """Parse one URL, without extracting nested URLs or following redirects."""
        if not url or len(url) > _MAX_URL_LENGTH:
            return None
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
            return None
        if "\\" in url:
            return None
        for parser in self._url_parsers:
            result = parser(url)
            if result is not None:
                return result
        return None
