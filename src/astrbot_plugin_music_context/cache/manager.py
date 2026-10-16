"""Bounded JSON snapshots with TTL, atomic replacement, and typed recovery."""

import json
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, TypeVar, cast, get_args, get_type_hints

from astrbot.api import logger as astrbot_logger

from ..models import MusicContext, MusicLyrics, MusicTrack
from ..models.context import LyricsPresentation, ProcessorMetadata
from .files import atomic_write

DEFAULT_TTL = 7 * 24 * 60 * 60
CacheValue = MusicContext | MusicLyrics | MusicTrack
Model = TypeVar("Model", MusicContext, MusicLyrics, MusicTrack)
_FILES = {
    MusicContext: "music_context.json",
    MusicLyrics: "lyrics.json",
    MusicTrack: "tracks.json",
}
# 歌曲/原始歌词缓存减少外部检索，Context 缓存减少重复构建或辅助模型调用；
# 都是可过期的歌曲数据，按请求追踪的历史由 HistoryStore 独立保存。
_FIELDS = {
    MusicTrack: get_type_hints(MusicTrack),
    MusicLyrics: get_type_hints(MusicLyrics),
}


def _input_source(value: object) -> str:
    return value if isinstance(value, str) and value in {"link", "card"} else "unknown"


def _duration(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("ttl must be finite and positive")  # noqa: TRY004 - configuration API
    try:
        duration = float(value)
    except OverflowError as exc:
        raise ValueError("ttl must be finite and positive") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("ttl must be finite and positive")
    return duration


def _decode(model: type[Model], payload: object, *, generated: bool = False) -> Model:
    """Reject incompatible cached fields instead of trusting JSON types."""
    if not isinstance(payload, dict):
        raise TypeError("Invalid cached model")
    if model is MusicContext:
        if not {"track", "lyrics"} <= set(payload) or set(payload) - set(
            get_type_hints(MusicContext)
        ):
            raise ValueError("Invalid context fields")
        presentation = payload.get("lyrics_presentation", {})
        defaults_p = asdict(LyricsPresentation())
        if not isinstance(presentation, dict) or set(presentation) - set(defaults_p):
            raise ValueError("Invalid lyrics presentation")
        presentation = {**defaults_p, **presentation}
        if (presentation["requested_mode"] not in ("unknown", "original", "bilingual")
            or presentation["effective_mode"] not in ("unknown", "original", "bilingual")
            or type(presentation["original_truncated"]) is not bool
            or type(presentation["translation_truncated"]) is not bool
            or presentation["translation_source"] not in (None, "netease")
            or presentation["fallback_reason"] not in (None, "translation_missing", "translation_unusable",
                "translation_language_unconfirmed", "translation_budget", "original_missing", "instrumental", "context_budget")):
            raise ValueError("Invalid lyrics presentation values")
        presentation = LyricsPresentation(**presentation)
        # Generated variants are opt-in; legacy factual entries keep their contract.
        raw = payload.get("original_lyrics")
        if raw is not None and not isinstance(raw, str):
            raise ValueError("Invalid original lyrics")
        metadata = payload.get("processor_metadata", {})
        defaults = asdict(ProcessorMetadata())
        if generated:
            if (
                not isinstance(metadata, dict) or set(metadata) != set(defaults)
                or metadata.get("enabled") is not True
                or metadata.get("generated_by") != "context_processor"
                or metadata.get("validation") != "schema_valid"
                or metadata.get("confidence") is not None
                or metadata.get("evidence") not in {"lyrics_text", "metadata_only"}
                or any(
                    not isinstance(metadata.get(key), str) or not metadata[key]
                    or len(metadata[key]) > 300
                    for key in ("model", "processed_at", "version")
                )
            ):
                raise ValueError("Invalid generated context metadata")
            for key, limit in (("summary", 240), ("artist_info", 160)):
                if not isinstance(payload.get(key), str) or len(payload[key]) > limit:
                    raise ValueError("Invalid generated text")
            tags = {}
            for key in ("style_tags", "emotion_tags", "theme_tags", "lyrics_topics"):
                items = payload.get(key)
                if (
                    not isinstance(items, (list, tuple)) or len(items) > 6
                    or any(
                        not isinstance(item, str) or not item.strip() or len(item) > 32
                        for item in items
                    )
                ):
                    raise ValueError("Invalid generated tags")
                tags[key] = tuple(items)
            if (
                payload.get("lyrics_summary") is not None
                or payload.get("scene_tags", []) not in ([], ())
                or payload.get("lyrics_structure", []) not in ([], ())
            ):
                raise ValueError("Unexpected generated fields")
            return cast(Model, MusicContext(
                track=_decode(MusicTrack, payload["track"]),
                lyrics=_decode(MusicLyrics, payload["lyrics"]),
                original_lyrics=raw, summary=payload["summary"],
                lyrics_presentation=presentation,
                artist_info=payload["artist_info"], **tags,
                processor_metadata=ProcessorMetadata(**metadata),
            ))
        if not isinstance(metadata, dict) or any(
            key not in defaults or value != defaults[key]
            for key, value in metadata.items()
        ):
            raise ValueError("Enhanced contexts are not persistent cache values")
        for key in ("summary", "artist_info", "lyrics_summary"):
            if payload.get(key) is not None:
                raise ValueError("Unexpected cached enhancement")
        for key in (
            "style_tags",
            "emotion_tags",
            "theme_tags",
            "scene_tags",
            "lyrics_topics",
            "lyrics_structure",
        ):
            if payload.get(key, []) not in ([], ()):
                raise ValueError("Unexpected cached tags")
        return cast(
            Model,
            MusicContext(
                track=_decode(MusicTrack, payload["track"]),
                lyrics=_decode(MusicLyrics, payload["lyrics"]),
                original_lyrics=raw,
                lyrics_presentation=presentation,
            ),
        )
    hints = _FIELDS[model]
    if model is MusicLyrics:
        # Only known optional omissions are upgraded. Keep the envelope's raw
        # shape until refresh so missing translation differs from explicit null.
        if "original" not in payload or set(payload) - set(hints):
            raise ValueError("Invalid legacy lyrics fields")
        payload = {**asdict(MusicLyrics()), **payload}
    if set(payload) != set(hints):
        raise ValueError("Invalid model fields")
    for name, annotation in hints.items():
        if type(payload[name]) not in (get_args(annotation) or (annotation,)):
            raise ValueError("Invalid model field type")
    return model(**payload)


def _key(platform: str, track_id: str, variant: str = "") -> str:
    # 不同平台可能复用同一 track_id，因此身份必须包含 platform + track_id。
    # 第三项 variant 隔离 Context 生成条件；JSON 数组编码避免简单拼接产生歧义。
    if (
        not platform
        or not track_id
        or not all(isinstance(value, str) for value in (platform, track_id, variant))
    ):
        raise ValueError("Cache identity must contain nonempty strings")
    return json.dumps([platform, track_id, variant], ensure_ascii=False)


def _identity(value: CacheValue) -> tuple[str, str] | None:
    track = value.track if isinstance(value, MusicContext) else value
    return (track.source, track.track_id) if isinstance(track, MusicTrack) else None


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Detached, validated management snapshot; no underlying file access."""

    kind: str
    platform: str
    track_id: str
    created_at: float
    expires_at: float
    value: CacheValue
    input_source: str = "unknown"


class CacheManager:
    """Persist only music models; reads never refresh the default seven-day TTL.

    One manager owns a directory, with thread-safe read/modify/write operations.
    Multiple processes/writers are not supported. Each snapshot defaults to at
    most 256 entries and 8 MiB; evict oldest writes when either limit is reached.
    No persistent in-memory replica is kept. Files are created lazily. Missing,
    invalid, expired, or unreadable data is a miss; failed writes return False.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        ttl: float = DEFAULT_TTL,
        max_entries: int = 256,
        max_file_bytes: int = 8 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
        logger: Any | None = None,
    ) -> None:
        duration = _duration(ttl)
        if type(max_entries) is not int or max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        if type(max_file_bytes) is not int or max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be a positive integer")
        self.directory = Path(directory)
        self.ttl = duration
        self.max_entries = max_entries
        self.max_file_bytes = max_file_bytes
        self.clock = clock
        self.logger = logger if logger is not None else astrbot_logger
        self._lock = threading.RLock()

    def get_context(
        self, platform: str, track_id: str, *, variant: str = ""
    ) -> MusicContext | None:
        return self._get(MusicContext, platform, track_id, variant)

    def get_lyrics(self, platform: str, track_id: str) -> MusicLyrics | None:
        return self._get(MusicLyrics, platform, track_id)

    def get_track(self, platform: str, track_id: str) -> MusicTrack | None:
        return self._get(MusicTrack, platform, track_id)

    def get(
        self, platform: str, track_id: str, *, variant: str = ""
    ) -> CacheValue | None:
        """Return the highest-priority hit: context, then lyrics, then track."""
        with self._lock:
            for model in _FILES:
                value = self._get(
                    model, platform, track_id, variant if model is MusicContext else ""
                )
                if value is not None:
                    return value
        return None

    def _get(
        self, model: type[Model], platform: str, track_id: str, variant: str = ""
    ) -> Model | None:
        try:
            key = _key(platform, track_id, variant)
        except ValueError:
            return None
        with self._lock:
            entry = self._load(model, self.clock()).get(key)
            return (
                _decode(model, entry["value"], generated=variant.startswith("generated:v1:"))
                if entry else None
            )

    def set(
        self,
        platform: str,
        track_id: str,
        value: CacheValue,
        *,
        ttl: float | None = None,
        variant: str = "",
        input_source: str = "unknown",
    ) -> bool:
        """Save a typed value; raw updates invalidate derived contexts.

        Context expiry is capped by existing track/lyrics expiry, preventing
        rebuilding from old evidence from extending that evidence's lifetime.
        Context variants distinguish processing budgets/versions and share URLs.
        """
        model = type(value)
        if model not in _FILES or (model is not MusicContext and variant):
            return False
        duration = _duration(self.ttl if ttl is None else ttl)
        try:
            key = _key(platform, track_id, variant)
            payload = asdict(value)
            decoded = _decode(model, payload, generated=variant.startswith("generated:v1:"))
            identity = _identity(decoded)
            if identity is not None and identity != (platform, track_id):
                return False
        except (ValueError, TypeError):
            return False
        with self._lock:
            now = self.clock()
            expires_at = now + duration
            if model is MusicContext:
                # 派生 Context 不得比其原始证据活得更久，防止反复构建延长旧歌词有效期。
                for dependency in (MusicTrack, MusicLyrics):
                    entry = self._load(dependency, now).get(_key(platform, track_id))
                    if entry:
                        expires_at = min(expires_at, entry["expires_at"])
            entry = {"created_at": now, "expires_at": expires_at, "value": payload}
            # 来源属于缓存写入的观测元数据，不混入 Provider 歌词模型。
            # 只在实际写入时记录；读取/缓存命中不追认或改写旧来源。
            if model is MusicLyrics:
                entry["input_source"] = _input_source(input_source)
            try:
                if len(self._encode({key: entry})) > self.max_file_bytes:
                    return False
            except (ValueError, UnicodeError):
                return False
            entries = self._load(model, now)
            entries.pop(key, None)
            entries[key] = entry
            saved = self._write(model, entries)
            if saved and model is not MusicContext:
                # 原始歌曲/歌词写入后，同一身份的事实和生成 Context 都失效，避免解释旧数据。
                self._invalidate_contexts(platform, track_id, now)
            return saved

    def claim_translation_refresh(self, platform: str, track_id: str) -> str | None:
        """Persist a bounded lease before I/O; explicit empty translations are known.

        At most three attempts per existing raw-entry lifetime, 300 seconds apart.
        Failed writes do not grant a lease. No TTL renewal or original mutation.
        """
        if platform != "netease":
            return None
        with self._lock:
            now = self.clock()
            entries = self._load(MusicLyrics, now)
            entry = entries.get(_key(platform, track_id))
            if not entry or "translation" in entry["value"]:
                return None
            old = _decode(MusicLyrics, entry["value"])
            if old.is_instrumental or not old.original:
                return None
            state = entry.get("translation_refresh", {})
            if not isinstance(state, dict):
                return None
            attempts, retry_at = state.get("attempts", 0), state.get("retry_at", 0)
            if (type(attempts) is not int or not 0 <= attempts < 3
                or type(retry_at) not in (int, float) or not math.isfinite(retry_at)
                or now < retry_at):
                return None
            ticket = uuid.uuid4().hex
            entry["translation_refresh"] = {"attempts": attempts + 1,
                "retry_at": min(now + 300, entry["expires_at"]), "ticket": ticket}
            # 补取是可选操作；不能为存放租约而让通用容量淘汰删除原歌词。
            # 检查编码后的字节数，超限不写盘也不授权网络请求。
            if len(self._encode(entries)) > self.max_file_bytes:
                return None
            return ticket if self._write(MusicLyrics, entries) else None

    def complete_translation_refresh(
        self, platform: str, track_id: str, ticket: str, fetched: MusicLyrics,
    ) -> bool:
        """Merge only into the leased legacy entry; preserve its original and TTL."""
        if platform != "netease" or fetched.source != "netease":
            return False
        try:
            _decode(MusicLyrics, asdict(fetched))
        except (ValueError, TypeError):
            return False
        with self._lock:
            now = self.clock()
            entries = self._load(MusicLyrics, now)
            entry = entries.get(_key(platform, track_id))
            if (not entry or entry.get("translation_refresh", {}).get("ticket") != ticket
                or "translation" in entry["value"]):
                return False
            old = _decode(MusicLyrics, entry["value"])
            merged = replace(old, translation=fetched.translation,
                             translation_language=fetched.translation_language,
                             source=old.source or fetched.source)
            entry["value"] = asdict(merged)
            entry.pop("translation_refresh", None)
            saved = self._write(MusicLyrics, entries)
            if saved:
                self._invalidate_contexts(platform, track_id, now)
            return saved

    def cleanup(self) -> None:
        """Remove expired/invalid entries and regenerate damaged snapshots."""
        with self._lock:
            now = self.clock()
            for model in _FILES:
                self._load(model, now)

    def snapshot(self) -> list[CacheEntry]:
        """Inspect valid entries under the same lock/TTL rules as normal reads."""
        result = []
        with self._lock:
            now = self.clock()
            for model, filename in _FILES.items():
                for key, entry in self._load(model, now).items():
                    platform, track_id, _variant = json.loads(key)
                    result.append(
                        CacheEntry(
                            filename.removesuffix(".json"),
                            platform,
                            track_id,
                            entry["created_at"],
                            entry["expires_at"],
                            _decode(
                                model, entry["value"],
                                generated=_variant.startswith("generated:v1:"),
                            ),
                            _input_source(entry.get("input_source")),
                        )
                    )
        return result

    def clear(self) -> bool:
        """Explicitly empty only the three owned snapshots, without recursive deletion."""
        with self._lock:
            # Evaluate every write even if one fails; report partial failure.
            results = [self._write(model, {}) for model in _FILES]
        return all(results)

    def configure(self, *, ttl: int, max_entries: int) -> None:
        duration = _duration(ttl)
        if type(max_entries) is not int or max_entries <= 0:
            raise ValueError("Invalid cache capacity")
        with self._lock:
            self.ttl = duration
            self.max_entries = max_entries

    def _invalidate_contexts(self, platform: str, track_id: str, now: float) -> None:
        entries = self._load(MusicContext, now)
        removed = [
            key for key in entries if json.loads(key)[:2] == [platform, track_id]
        ]
        for key in removed:
            entries.pop(key)
        if removed:
            self._write(MusicContext, entries)

    def _load(self, model: type[CacheValue], now: float) -> dict:
        path = self.directory / _FILES[model]
        try:
            with path.open("rb") as stream:
                raw = stream.read(self.max_file_bytes + 1)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            self._log("read", exc)
            return {}
        try:
            if len(raw) > self.max_file_bytes:
                raise ValueError("Oversized cache snapshot")
            data = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(data, dict)
                or type(data.get("version")) is not int
                or data["version"] != 1
                or not isinstance(data.get("entries"), dict)
            ):
                raise ValueError("Invalid cache envelope")
            entries = data["entries"]
        except (ValueError, UnicodeError, RecursionError) as exc:
            self._log("decode", exc)
            self._write(model, {})
            return {}
        valid = {}
        for key, entry in entries.items():
            try:
                identity = json.loads(key)
                if (
                    not isinstance(identity, list)
                    or len(identity) != 3
                    or _key(*identity) != key
                    or not isinstance(entry, dict)
                ):
                    raise ValueError("Invalid cache key/entry")
                created, expires = entry["created_at"], entry["expires_at"]
                if (
                    type(created) not in (int, float)
                    or type(expires) not in (int, float)
                    or not math.isfinite(created)
                    or not math.isfinite(expires)
                    or not created <= now < expires
                ):
                    raise ValueError("Expired or invalid timestamps")
                value = _decode(
                    model, entry["value"], generated=identity[2].startswith("generated:v1:"),
                )
                track_identity = _identity(value)
                if track_identity is not None and track_identity != tuple(identity[:2]):
                    raise ValueError("Mismatched track identity")
                valid[key] = entry
            except (KeyError, ValueError, TypeError, RecursionError, OverflowError):
                continue
        if len(valid) != len(entries) or len(valid) > self.max_entries:
            self._write(model, valid)
        return valid

    @staticmethod
    def _encode(entries: dict) -> bytes:
        return json.dumps(
            {"version": 1, "entries": entries},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _write(self, model: type[CacheValue], entries: dict) -> bool:
        # 按写入时间淘汰，读取不会续期；条数和文件体积共同约束音乐缓存的磁盘使用。
        path = self.directory / _FILES[model]
        try:
            while len(entries) > self.max_entries:
                entries.pop(min(entries, key=lambda key: entries[key]["created_at"]))
            raw = self._encode(entries)
            while len(raw) > self.max_file_bytes and entries:
                entries.pop(min(entries, key=lambda key: entries[key]["created_at"]))
                raw = self._encode(entries)
            if len(raw) > self.max_file_bytes:
                return False
            atomic_write(path, raw)
            return True
        except (OSError, ValueError, UnicodeError) as exc:
            self._log("write", exc)
            return False

    def _log(self, operation: str, exc: Exception) -> None:
        self.logger.warning("Music cache %s failed (%s)", operation, type(exc).__name__)
