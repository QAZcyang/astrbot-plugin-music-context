"""Bounded music observations with optional lyric-free JSON persistence."""

import copy
import math
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..cache.history import HistoryStore
from ..config import PluginConfig
from ..models import MusicContext

_SECRET_PATTERNS = (
    r"https?://[^\s]+",
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"(?<!\w)\+?\d[\d ()-]{5,}\d(?!\w)",
    r"(?:Bearer\s+|sk[-_])[A-Za-z0-9_.-]+",
    r"""(?:\b(?:api[_-]?key|password|token|secret|cookie|MUSIC_U)|密码|令牌)["']?\s*[:=：]\s*["']?[^\s,;"'}]+""",
)


def safe_text(value: object, limit: int = 2000) -> str:
    """Bound and redact common secret/identifier forms; not semantic anonymization."""
    if not isinstance(value, str):
        return ""
    # Bound regex work as well as stored output. Redact before final truncation.
    text = value[: max(limit * 2, 4096)]
    lower = text.lower()
    possible = (
        "http://" in lower or "https://" in lower,
        "@" in text,
        any(char.isdigit() for char in text),
        "bearer" in lower or "sk-" in lower or "sk_" in lower,
        any(word in lower for word in ("api", "password", "token", "secret", "cookie", "music_u", "密码", "令牌")),
    )
    for pattern, present in zip(_SECRET_PATTERNS, possible):
        if present:
            text = re.sub(pattern, "[redacted]", text, flags=re.IGNORECASE)
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text[:limit]


def public_context(context: MusicContext) -> dict:
    """Keep the shared model shape while excluding personal share URL parameters."""
    data = context.payload()
    data["track"]["url"] = None
    for key in ("title", "artist", "album"):
        value = data["track"][key]
        data["track"][key] = safe_text(value, 300) if value is not None else None
    for key in ("original", "translation"):
        value = data["lyrics"][key]
        data["lyrics"][key] = safe_text(value, 32000) if value is not None else None
    for key in ("summary", "lyrics_summary", "song_summary", "artist_info"):
        if key in data:
            data[key] = safe_text(data[key], 1000)
    for key in (
        "style", "emotion", "theme", "keywords",
        "style_tags",
        "emotion_tags",
        "theme_tags",
        "scene_tags",
        "lyrics_topics",
        "lyrics_structure",
    ):
        if key in data:
            data[key] = [safe_text(value, 100) for value in data[key]]
    return data


@dataclass(frozen=True, slots=True)
class ContextTicket:
    # identifier 关联本次 Context；owner/epoch 拒绝跨实例、清空前或关闭收集前的迟到回调。
    identifier: int
    history_epoch: int
    owner: str


class ManagementState:
    """Observe music requests only; keep Debug in memory and history on disk."""

    def __init__(
        self, settings: PluginConfig, *, clock: Callable[[], float] = time.time,
        history_directory: str | Path | None = None,
    ):
        self.settings = settings
        self.clock = clock
        self.active = True
        self._contexts: deque[dict] = deque(maxlen=settings.max_history_items)
        self._history: deque[dict] = deque(maxlen=self.reply_limit)
        self._sequence = 0
        self._history_epoch = 0
        self._owner = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self._store = HistoryStore(history_directory) if history_directory is not None else None
        self._revision = 0
        self._saved_revision = -1
        if self._store is not None:
            self._restore(self._store.load())

    @property
    def reply_limit(self) -> int:
        return min(self.settings.max_history_items, self.settings.llm_history_limit)

    @property
    def replies_enabled(self) -> bool:
        return self.settings.history_enable and self.settings.store_llm_history

    @property
    def persistent(self) -> bool:
        return self._store is not None and self._store.available

    def record_context(
        self, context: MusicContext, *, diagnostics: dict | None = None
    ) -> ContextTicket | None:
        data = public_context(context)
        with self._lock:
            if not self.active or not self.settings.history_enable:
                return None
            self._sequence += 1
            metadata = data.get("processor_metadata", {})
            processing = (diagnostics or {}).get("processing", {})
            retrieval = (diagnostics or {}).get("retrieval", {})
            row = {
                "id": self._sequence,
                "generated_at": self.clock(),
                "context": data,
                "has_lyrics": bool(data["lyrics"]["original"]),
                "has_translation": bool(data["lyrics"]["translation"]),
                "retrieval": {
                    # Whitelist safe request facts; do not retain raw data in Debug off.
                    key: retrieval.get(key) if retrieval.get(key) in allowed else "unknown"
                    for key, allowed in {
                        "context": {"cache", "built", "generated_cache", "generated"},
                        "lyrics": {"cache", "context_cache", "provider", "unavailable"},
                    }.items()
                },
                "injection": copy.deepcopy((diagnostics or {}).get("injection", {
                    "status": "observed", "length": None,
                })),
                "processing": {
                    "enabled": self.settings.enable_context_processor or bool(metadata.get("enabled")),
                    "status": processing.get(
                        "status", "success" if metadata.get("enabled") else "disabled"
                    ),
                    "model": metadata.get("model") or processing.get("model"),
                    "processed_at": metadata.get("processed_at") or processing.get("processed_at"),
                    "error": processing.get("error"),
                },
                "sources": {
                    "raw": {
                        "generated_by": "provider",
                        "source": data["track"]["source"],
                        "fields": ["title", "artist", "album", "lyrics", "source"],
                    },
                    "processed": {
                        **metadata,
                        "fields": ["song_summary", "artist_info", "emotion", "theme", "style", "keywords"],
                    } if metadata.get("enabled") else None,
                },
            }
            if self.settings.debug_mode and (diagnostics or {}).get("debug"):
                row["debug"] = copy.deepcopy(diagnostics["debug"])
                truncated = False
                for key in ("raw_lyrics", "raw_translation"):
                    text = row["debug"].get(key)
                    if isinstance(text, str) and len(text) > 64000:
                        row["debug"][key] = text[:64000]
                        truncated = True
                row["debug"]["raw_display_truncated"] = truncated
            self._contexts.append(row)
            # Persistent capacity is independent of the existing Debug capture bound.
            for old in list(self._contexts)[:-20]:
                old.pop("debug", None)
            self._revision += 1
            return ContextTicket(self._sequence, self._history_epoch, self._owner)

    def record_reply(
        self, ticket: ContextTicket, reply: object, input_length: int,
        *, user_message: object = "", model_name: object = None,
    ) -> None:
        if not self.replies_enabled:
            return
        # Reply history is the callback's original text, not a display summary.
        # Redaction/truncation would also corrupt Markdown URLs and long paragraphs.
        # 用户请求保留脱敏限长文本与来源快照；LLM 回复保留回调原文，供 WebUI 完整追踪。
        # 同一请求重复回调替换为最新文本，不作为每轮对话或客户端送达的完整日志。
        text = reply if isinstance(reply, str) else ""
        if not text.strip():
            return
        with self._lock:
            if (
                not self.active
                or not self.replies_enabled
                or ticket.history_epoch != self._history_epoch
                or ticket.owner != self._owner
            ):
                return
            context = next(
                (row for row in self._contexts if row["id"] == ticket.identifier), None
            )
            if context is None:
                return
            # A request may have multiple LLM/tool callbacks: keep its latest text once.
            previous = next(
                (row for row in self._history if row["id"] == ticket.identifier), None
            )
            if previous is not None:
                self._history.remove(previous)
            self._history.append(
                {
                    "id": ticket.identifier,
                    "time": self.clock(),
                    "input_summary": f"音乐分享消息；文本长度 {max(0, input_length)} 字符",
                    "context": copy.deepcopy(context["context"]),
                    "retrieval": copy.deepcopy(context["retrieval"]),
                    "reply": text,
                    "user_message": safe_text(user_message),
                    "model_name": safe_text(model_name, 200) or None,
                }
            )
            self._revision += 1

    def contexts(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(reversed(self._contexts)))

    def configure(self, settings: PluginConfig) -> None:
        with self._lock:
            if not settings.debug_mode:
                for row in self._contexts:
                    row.pop("debug", None)
            was_enabled = self.replies_enabled
            self.settings = settings
            if was_enabled and not self.replies_enabled:
                # Disabling capture retains existing history; invalidate in-flight tickets.
                self._history_epoch += 1
            self._contexts = deque(
                list(self._contexts)[-settings.max_history_items :],
                maxlen=settings.max_history_items,
            )
            self._history = deque(
                list(self._history)[-self.reply_limit :], maxlen=self.reply_limit,
            )
            self._revision += 1

    def history(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(reversed(self._history)))

    def clear_history(self) -> bool:
        with self._lock:
            self._history.clear()
            self._history_epoch += (
                1  # Old in-flight responses cannot restore cleared data.
            )
            self._revision += 1
        return self.persist(force=True)

    def close(self) -> None:
        with self._lock:
            self.active = False
            self._history.clear()
            self._history_epoch += 1
            self._contexts.clear()

    def persist(self, *, force: bool = False) -> bool:
        """Called in a filesystem worker after observation; never hold state lock for I/O."""
        # 锁内制作脱离状态的快照，锁外写盘，避免慢磁盘阻塞观察；revision 让失败后仍可重试。
        if self._store is None:
            return True
        with self._persist_lock:
            with self._lock:
                if not self.active or (not force and not self.settings.history_enable):
                    return True
                revision = self._revision
                if revision == self._saved_revision:
                    return True
                data = {
                    "version": 1, "sequence": self._sequence,
                    "contexts": [self._context_record(row) for row in self._contexts],
                    "replies": [self._reply_record(row) for row in self._history],
                }
            saved = self._store.save(data)
            if saved:
                self._saved_revision = revision
            return saved

    @staticmethod
    def _context_record(row: dict) -> dict:
        # 磁盘只保留歌曲身份、结果、来源和注入状态，不重复存储歌词、完整 Context 或 Debug。
        # 因此重启恢复的历史能展示追踪信息，但不能代替可复用的 Context 缓存。
        context = row["context"]
        return {
            "context_id": row["id"], "created_at": row["generated_at"],
            "music": {key: context["track"].get(key) for key in (
                "title", "artist", "album", "source", "track_id",
            )},
            "result": {
                "summary": context.get("song_summary") or context.get("summary"),
                "style_tags": context.get("style", []),
                "emotion_tags": context.get("emotion", []),
                "theme_tags": context.get("theme", []),
                "keywords": context.get("keywords", []),
                "artist_info": context.get("artist_info"),
            },
            "processor": {**row["processing"], "processed_time": row["processing"].get("processed_at")},
            "quality": context.get("processor_metadata", {}),
            "has_lyrics": row["has_lyrics"], "has_translation": row["has_translation"],
            "retrieval": row["retrieval"],
            "injection": {key: row["injection"].get(key) for key in ("status", "length")},
        }

    @staticmethod
    def _reply_record(row: dict) -> dict:
        return {
            "reply_id": row["id"], "context_id": row["id"], "created_at": row["time"],
            "user_message": row["user_message"], "assistant_reply": row["reply"],
            "model_name": row["model_name"], "input_summary": row["input_summary"],
            # A Context can be evicted before its reply; keep only its song association.
            "music": {key: row["context"]["track"].get(key) for key in (
                "title", "artist", "album", "source", "track_id",
            )},
            "retrieval": row["retrieval"],
        }

    def _restore(self, data: dict) -> None:
        """Validate stored records before constructing the existing WebUI projections."""
        # 恢复记录与单调 ID，不恢复 ticket 的实例 owner；旧记录不会授权新实例的回复回调。
        self._sequence = data["sequence"]
        for kind in ("contexts", "replies"):
            seen = set()
            for record in data[kind]:
                try:
                    self._validate_record(record, kind)
                    identifier = record["context_id"]
                    if identifier in seen:
                        raise ValueError("Duplicate history identifier")
                    seen.add(identifier)
                    self._sequence = max(self._sequence, identifier)
                    track = {**record["music"], "url": None}
                    retrieval = record["retrieval"]
                    if kind == "contexts":
                        result = record["result"]
                        context = {
                            "track": track, "lyrics": {"original": None, "translation": None},
                            "song_summary": result["summary"], "artist_info": result["artist_info"],
                            "style": result["style_tags"], "emotion": result["emotion_tags"],
                            "theme": result["theme_tags"], "keywords": result["keywords"],
                            "processor_metadata": record["quality"],
                        }
                        self._contexts.append({
                            "id": identifier, "generated_at": record["created_at"],
                            "context": context, "has_lyrics": record["has_lyrics"],
                            "has_translation": record["has_translation"],
                            "retrieval": retrieval, "processing": record["processor"],
                            "injection": record["injection"],
                            "sources": {
                                "raw": {"generated_by": "provider", "source": track["source"],
                                        "fields": ["title", "artist", "album", "lyrics", "source"]},
                                "processed": record["quality"] or None,
                            },
                        })
                    else:
                        self._history.append({
                            "id": identifier, "time": record["created_at"],
                            "context": {"track": track}, "retrieval": retrieval,
                            "reply": record["assistant_reply"], "user_message": record["user_message"],
                            "model_name": record["model_name"], "input_summary": record["input_summary"],
                        })
                except (KeyError, ValueError, TypeError, OverflowError) as exc:
                    self._store.log("record decode", exc)
        # Prune invalid/oversized history at startup, preserving the monotonic IDs.
        self.persist(force=True)

    @staticmethod
    def _validate_record(record: dict, kind: str) -> None:
        if not isinstance(record, dict):
            raise ValueError("Invalid history record")
        for key in ("music", "retrieval"):
            if not isinstance(record[key], dict):
                raise ValueError("Invalid song association")
        if type(record["context_id"]) is not int or not 0 < record["context_id"] < 10**12:
            raise ValueError("Invalid identifier")
        stamp = record["created_at"]
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
            raise ValueError("Invalid timestamp")
        for key in ("title", "artist", "album", "source", "track_id"):
            value = record["music"][key]
            if value is not None and (not isinstance(value, str) or len(value) > 300):
                raise ValueError("Invalid song metadata")
        for value in record["retrieval"].values():
            if value not in {"cache", "built", "generated_cache", "generated", "context_cache", "provider", "unavailable", "unknown"}:
                raise ValueError("Invalid retrieval metadata")
        if set(record["retrieval"]) != {"context", "lyrics"}:
            raise ValueError("Incomplete retrieval metadata")
        if kind == "contexts":
            if not isinstance(record["result"], dict):
                raise ValueError("Invalid result")
            for key in ("has_lyrics", "has_translation"):
                if type(record[key]) is not bool:
                    raise ValueError("Invalid lyric availability")
            for key in ("style_tags", "emotion_tags", "theme_tags", "keywords"):
                tags = record["result"][key]
                if not isinstance(tags, list) or len(tags) > 100 or any(
                    not isinstance(tag, str) or len(tag) > 100 for tag in tags
                ):
                    raise ValueError("Invalid result tags")
            for key in ("summary", "artist_info"):
                value = record["result"][key]
                if value is not None and (not isinstance(value, str) or len(value) > 1000):
                    raise ValueError("Invalid result text")
            for key in ("processor", "quality", "injection"):
                if not isinstance(record[key], dict):
                    raise ValueError("Invalid observation metadata")
            if not isinstance(record["processor"]["status"], str):
                raise ValueError("Invalid processor status")
            processor = record["processor"]
            if set(processor) - {"enabled", "status", "model", "processed_at", "processed_time", "error"}:
                raise ValueError("Unexpected processor data")
            if type(processor["enabled"]) is not bool:
                raise ValueError("Invalid processor enabled state")
            for key in ("status", "model", "processed_at", "processed_time", "error"):
                value = processor.get(key)
                if value is not None and (not isinstance(value, str) or len(value) > 300):
                    raise ValueError("Invalid processor metadata")
            length = record["injection"].get("length")
            if length is not None and (type(length) is not int or length < 0):
                raise ValueError("Invalid injection length")
            if record["injection"].get("status") not in {"injected", "observed"}:
                raise ValueError("Invalid injection state")
            quality = record["quality"]
            if set(quality) - {"enabled", "model", "processed_at", "version", "generated_by",
                              "confidence", "validation", "evidence"}:
                raise ValueError("Unexpected quality data")
            if quality and type(quality.get("enabled")) is not bool:
                raise ValueError("Invalid quality state")
            for key in ("model", "processed_at", "version", "generated_by", "validation", "evidence"):
                value = quality.get(key)
                if value is not None and (not isinstance(value, str) or len(value) > 300):
                    raise ValueError("Invalid quality metadata")
            confidence = quality.get("confidence")
            if confidence is not None and (type(confidence) not in (int, float)
                                           or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError("Invalid confidence")
        else:
            if record["reply_id"] != record["context_id"]:
                raise ValueError("Invalid reply identifier")
            if not isinstance(record["assistant_reply"], str):
                raise ValueError("Invalid assistant reply")
            for key, bound in (("user_message", 2000),
                               ("input_summary", 300), ("model_name", 200)):
                value = record[key]
                if value is not None and (not isinstance(value, str) or len(value) > bound):
                    raise ValueError("Invalid reply text")
