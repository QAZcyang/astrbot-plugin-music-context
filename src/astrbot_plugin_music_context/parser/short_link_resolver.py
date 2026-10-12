"""Bounded NetEase redirects with exact hosts, public DNS, and pinned sockets."""

import http.client
import ipaddress
import logging
import math
import queue
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

_HOSTS = {"163cn.tv", "music.163.com"}
_REDIRECTS = {301, 302}
_MAX_URL_CHARS = 2048


def _checked_url(url: str, *, mobile: bool = False) -> str:
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_CHARS:
        raise ValueError("Invalid URL length")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("Invalid URL characters")
    if "\\" in url:
        raise ValueError("Invalid URL separator")
    parsed = urlsplit(url)
    hosts = _HOSTS | {"y.music.163.com"} if mobile else _HOSTS
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in hosts:
        raise ValueError("Unsupported URL host or scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are forbidden")
    if parsed.port not in (None, 443 if parsed.scheme == "https" else 80):
        raise ValueError("Nondefault port is forbidden")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.hostname,
            parsed.path or "/",
            parsed.query,
            parsed.fragment,
        )
    )


def _destination(current: str, location: str) -> str:
    if not isinstance(location, str) or not location:
        raise ValueError("Missing redirect location")
    # Validate Location before urljoin can discard leading control characters.
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in location):
        raise ValueError("Invalid redirect location")
    target = _checked_url(urljoin(current, location), mobile=True)
    parsed = urlsplit(target)
    if parsed.hostname == "y.music.163.com":
        # Authorized normalization only: no DNS lookup or connection to y.*.
        if parsed.path != "/m/song" or parsed.fragment:
            raise ValueError("Unsupported mobile share route")
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=64)
        ids = query.get("id", [])
        if not ids or len(set(ids)) != 1 or re.fullmatch(r"[0-9]+", ids[0]) is None:
            raise ValueError("Invalid mobile song ID")
        identifier = ids[0].lstrip("0")
        if not identifier:
            raise ValueError("Invalid mobile song ID")
        return "https://music.163.com/song?id=" + identifier
    return target


@dataclass(frozen=True, slots=True)
class RedirectResponse:
    """A single HTTP response header result; never contains a response body."""

    status: int
    location: str | None = None


class _PinnedConnection(http.client.HTTPConnection):
    """Connect to validated DNS answers while retaining the original HTTP host."""

    def __init__(self, host: str, port: int, timeout: float, secure: bool) -> None:
        self.default_port = 443 if secure else 80
        super().__init__(host, port, timeout=timeout)
        self.secure = secure

    def connect(self) -> None:
        answers = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not answers:
            raise OSError("No DNS addresses")
        for _, _, _, _, endpoint in answers:
            address = ipaddress.ip_address(endpoint[0])
            if not address.is_global:
                raise ValueError("Nonpublic DNS address")
        # No second lookup, proxies, or automatic redirect mechanism.
        family, kind, protocol, _, endpoint = answers[0]
        connection = socket.socket(family, kind, protocol)
        connection.settimeout(self.timeout)
        try:
            connection.connect(endpoint)
            if self.secure:
                connection = ssl.create_default_context().wrap_socket(
                    connection, server_hostname=self.host
                )
            self.sock = connection
        except BaseException:
            connection.close()
            raise


def _request_headers(url: str, timeout: float) -> RedirectResponse:
    parsed = urlsplit(_checked_url(url))
    secure = parsed.scheme == "https"
    connection = _PinnedConnection(
        parsed.hostname, 443 if secure else 80, timeout, secure
    )
    try:
        route = urlunsplit(("", "", parsed.path, parsed.query, ""))
        connection.request(
            "GET",
            route,
            headers={"Connection": "close", "User-Agent": "AstrBot-Music-Context/0.1"},
        )
        response = connection.getresponse()
        try:
            return RedirectResponse(response.status, response.getheader("Location"))
        finally:
            response.close()
    finally:
        connection.close()


HeaderRequester = Callable[[str, float], RedirectResponse]


class ShortLinkResolver:
    """Resolve 163cn.tv only: at most five 301/302 redirects and three seconds.

    A public music.163.com navigation target is returned without fetching its
    page. The Parser validates song syntax. Invalid/expired/failing links return
    None. The caller's total wait includes DNS and headers, not just socket I/O.
    A daemon worker may outlive timeout; it cannot start another hop after the
    deadline. No response body, share token, DNS answer, or URL is logged.
    """

    def __init__(
        self,
        *,
        request_headers: HeaderRequester | None = None,
        timeout: float = 3.0,
        max_redirects: int = 5,
        logger: logging.Logger | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 3
        ):
            raise ValueError("timeout must be positive and at most three seconds")
        if type(max_redirects) is not int or not 0 <= max_redirects <= 5:
            raise ValueError("max_redirects must be between zero and five")
        self.timeout = float(timeout)
        self.max_redirects = max_redirects
        self._request_headers = (
            request_headers if request_headers is not None else _request_headers
        )
        self.logger = logger if logger is not None else logging.getLogger(__name__)

    @staticmethod
    def supports(url: str) -> bool:
        try:
            parsed = urlsplit(_checked_url(url))
            return (
                parsed.hostname == "163cn.tv"
                and re.fullmatch(r"/[A-Za-z0-9_-]{1,128}/?", parsed.path) is not None
            )
        except (ValueError, TypeError):
            return False

    def resolve(self, url: str) -> str | None:
        if not self.supports(url):
            return None
        deadline = time.monotonic() + self.timeout
        result: queue.Queue[str | Exception | None] = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                result.put(self._follow(_checked_url(url), deadline))
            except Exception as exc:  # noqa: BLE001 - optional input must fail open
                result.put(exc)

        threading.Thread(target=work, name="music-short-link", daemon=True).start()
        try:
            value = result.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            self.logger.warning("Music short link resolution failed (TimeoutError)")
            return None
        if isinstance(value, Exception):
            self.logger.warning(
                "Music short link resolution failed (%s)", type(value).__name__
            )
            return None
        return value

    def _follow(self, url: str, deadline: float) -> str | None:
        seen: set[str] = set()
        for hop in range(self.max_redirects + 1):
            if url in seen:
                raise ValueError("Redirect loop")
            seen.add(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Resolution deadline")
            response = self._request_headers(url, remaining)
            if response.status not in _REDIRECTS:
                return None
            if hop == self.max_redirects:
                raise ValueError("Redirect limit")
            target = _destination(url, response.location)
            if urlsplit(target).hostname == "music.163.com":
                return target
            url = target
