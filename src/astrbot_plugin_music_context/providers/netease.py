"""NetEase legacy endpoint adapter without AstrBot or message dependencies.

These undocumented endpoints have no stability or rate-limit guarantee. HTTP
200 alone is not success: the body code, track identity and field types matter.
No login, third-party service, retries or cache are required by this adapter.

Lyrics remain raw, including timestamp-only translation shells; semantic
emptiness and cleaning belong to M-005. Language tags are left unknown.
Failures raise NetEaseProviderError; callers must handle them independently of
normal chat. Coroutine cancellation propagates. The default urllib transport
runs in a worker thread: cancelling its await cannot forcibly stop that thread;
socket timeouts and a response-size limit still apply.
"""

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import MusicLyrics, MusicTrack
from ..parser import MusicLinkResult, MusicParser
from .base import MusicProvider
from .capability import ProviderCapability
from .diagnostics import observe_response

JSONRequester = Callable[[str, float], Awaitable[Mapping[str, object]]]
_BASE_URL = "https://music.163.com"
_MAX_RESPONSE_BYTES = 256 * 1024


class NetEaseProviderError(RuntimeError):
    """Expected retrieval failure with a stable kind and no response-body dump."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _read_json(url: str, timeout: float) -> Mapping[str, object]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise NetEaseProviderError("invalid_response", "Response size limit exceeded")
    return json.loads(raw.decode("utf-8"))


async def _default_request_json(url: str, timeout: float) -> Mapping[str, object]:
    # urllib 属于同步 I/O，移至线程避免等待网络时阻塞事件循环；取消等待无法强停线程。
    return await asyncio.to_thread(_read_json, url, timeout)


def _normalize_track_id(track_id: str) -> str:
    if not isinstance(track_id, str) or re.fullmatch(r"[0-9]+", track_id) is None:
        raise NetEaseProviderError("invalid_id", "Expected a positive numeric track ID")
    normalized = track_id.lstrip("0")
    if not normalized:
        raise NetEaseProviderError("invalid_id", "Expected a positive numeric track ID")
    return normalized


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NetEaseProviderError("invalid_response", f"Invalid {name} field")
    return value or None


def _flag(payload: Mapping[str, object], name: str) -> bool:
    value = payload.get(name)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise NetEaseProviderError("invalid_response", f"Invalid {name} flag")
    return value


def _lyric_text(payload: Mapping[str, object], name: str) -> str | None:
    block = payload.get(name)
    if block is None:
        return None
    if not isinstance(block, Mapping):
        raise NetEaseProviderError("invalid_response", f"Invalid {name} block")
    return _optional_text(block.get("lyric"), name + ".lyric")


class NetEaseProvider(MusicProvider):
    """Fetch MusicTrack and MusicLyrics by ID using an injectable async requester.

    Metadata uses a generated song URL unless the caller supplies the original
    share URL. An injected requester receives (endpoint_url, timeout_seconds)
    and returns decoded JSON; it owns its transport and resource lifecycle.
    """

    name = "netease"
    priority = 100
    capabilities = ProviderCapability(
        platforms=frozenset({"netease"}),
        data_types=frozenset({"track", "lyrics"}),
        input_types=frozenset({"song_id", "url", "share_link", "card_message"}),
        priority=100,
    )

    @classmethod
    def create_default(cls) -> "NetEaseProvider":
        """Preserve the plugin's three-second request timeout on discovery."""
        return cls(timeout=3.0)

    def parse_url(self, url: str) -> MusicLinkResult | None:
        """Reuse the existing local NetEase parser without short-link I/O."""
        return MusicParser(resolve_short_links=False).parse_url(url)

    def __init__(
        self,
        request_json: JSONRequester | None = None,
        timeout: float = 5.0,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be finite and positive")
        self._request_json = (
            request_json if request_json is not None else _default_request_json
        )
        self._timeout = float(timeout)

    async def _request(
        self, path: str, params: Mapping[str, str]
    ) -> Mapping[str, object]:
        url = _BASE_URL + path + "?" + urlencode(params)
        try:
            payload = await asyncio.wait_for(
                self._request_json(url, self._timeout), timeout=self._timeout
            )
        except NetEaseProviderError:
            raise
        except TimeoutError as exc:
            raise NetEaseProviderError("timeout", "NetEase request timed out") from exc
        except HTTPError as exc:
            exc.close()
            raise NetEaseProviderError("http", f"HTTP status {exc.code}") from exc
        except URLError as exc:
            kind = "timeout" if isinstance(exc.reason, TimeoutError) else "network"
            raise NetEaseProviderError(kind, "NetEase transport failed") from exc
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise NetEaseProviderError(
                "invalid_response", "Invalid JSON response"
            ) from exc
        except OSError as exc:
            raise NetEaseProviderError("network", "NetEase transport failed") from exc
        except Exception as exc:
            raise NetEaseProviderError(
                "request_failed", "NetEase request failed"
            ) from exc

        observe_response(path, payload)
        if not isinstance(payload, Mapping) or type(payload.get("code")) is not int:
            raise NetEaseProviderError("invalid_response", "Missing response code")
        if payload["code"] != 200:
            raise NetEaseProviderError(
                "upstream", "NetEase returned an unsuccessful code"
            )
        return payload

    async def fetch_metadata(
        self, track_id: str, *, url: str | None = None
    ) -> MusicTrack:
        """Fetch verified metadata, or raise for missing tracks and failed requests."""
        track_id = _normalize_track_id(track_id)
        payload = await self._request("/api/song/detail/", {"ids": f"[{track_id}]"})
        songs = payload.get("songs")
        if not isinstance(songs, list):
            raise NetEaseProviderError("invalid_response", "Invalid songs field")
        if not songs:
            raise NetEaseProviderError("not_found", "Track was not found")
        song = songs[0]
        if not isinstance(song, Mapping):
            raise NetEaseProviderError("invalid_response", "Invalid track object")
        song_id = song.get("id")
        if type(song_id) not in (str, int) or str(song_id).lstrip("0") != track_id:
            raise NetEaseProviderError("invalid_response", "Track identity mismatch")
        title = _optional_text(song.get("name"), "name")
        if title is None or not title.strip():
            raise NetEaseProviderError("invalid_response", "Missing track title")
        artists = song.get("artists", [])
        if not isinstance(artists, list):
            raise NetEaseProviderError("invalid_response", "Invalid artists field")
        artist_names: list[str] = []
        for artist in artists:
            if not isinstance(artist, Mapping):
                raise NetEaseProviderError("invalid_response", "Invalid artist object")
            name = _optional_text(artist.get("name"), "artist.name")
            if name is not None:
                artist_names.append(name)
        album = song.get("album")
        if album is not None and not isinstance(album, Mapping):
            raise NetEaseProviderError("invalid_response", "Invalid album field")
        album_name = _optional_text(album.get("name"), "album.name") if album else None
        return MusicTrack(
            title=title,
            artist=" / ".join(artist_names),
            album=album_name,
            source="netease",
            track_id=track_id,
            url=url if url is not None else f"{_BASE_URL}/song?id={track_id}",
        )

    async def fetch_lyrics(self, track_id: str) -> MusicLyrics:
        """Fetch raw lyrics; empty content and explicit pure music are valid results."""
        # 保留原始 LRC/翻译给 Cleaner，便于缓存复用；只有 pureMusic 明示时才标为纯音乐，
        # 缺歌词不等于纯音乐，也不能据此推断用户情绪或意图。
        track_id = _normalize_track_id(track_id)
        payload = await self._request(
            "/api/song/lyric", {"id": track_id, "lv": "1", "kv": "1", "tv": "-1"}
        )
        if _flag(payload, "uncollected"):
            raise NetEaseProviderError("unavailable", "Lyrics are not collected")
        if _flag(payload, "pureMusic"):
            return MusicLyrics(source="netease", is_instrumental=True)
        return MusicLyrics(
            original=_lyric_text(payload, "lrc"),
            translation=_lyric_text(payload, "tlyric"),
            source="netease",
        )
