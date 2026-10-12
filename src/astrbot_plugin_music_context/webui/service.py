"""Management reads and explicit maintenance commands, with no music retrieval."""

from dataclasses import asdict

from ..cache import CacheManager
from ..config_service import ConfigService
from ..context.processor import ContextProcessor
from ..lyrics import clean_lyrics
from ..models import MusicContext, MusicLyrics, MusicTrack
from ..providers import ProviderRegistry
from .state import ManagementState, safe_text


class DashboardService:
    def __init__(
        self,
        state: ManagementState,
        registry: ProviderRegistry,
        cache: CacheManager | None,
        *,
        version: str = "1.0.2",
    ):
        self.state = state
        self.registry = registry
        self.cache = cache
        self.version = version
        self.config_service: ConfigService | None = None
        self.processor: ContextProcessor | None = None

    def overview(self) -> dict:
        entries = self.cache.snapshot() if self.cache is not None else []
        counts = {
            kind: sum(entry.kind == kind for entry in entries)
            for kind in ("tracks", "lyrics", "music_context")
        }
        providers = []
        for provider in self.registry.providers_by_priority():
            capability = self.registry.get_capabilities(provider.name)
            if capability is not None:
                providers.append(
                    {
                        "name": provider.name,
                        "status": "registered",
                        "platforms": sorted(capability.platforms),
                        "priority": capability.priority,
                    }
                )
        return {
            "name": "Music Context",
            "version": self.version,
            "status": "running" if self.state.active else "stopped",
            "cache_counts": counts,
            "cache_count": sum(counts.values()),
            "cache_available": self.cache is not None,
            "providers": providers,
            "settings": asdict(self.state.settings),
            "context_count": len(self.state.contexts()),
            "history_count": len(self.state.history()),
            "history_persistent": self.state.persistent,
        }

    def lyrics(self, *, offset: int = 0, limit: int = 50) -> dict:
        entries = self.cache.snapshot() if self.cache is not None else []
        tracks = {}
        for entry in entries:
            value = (
                entry.value.track
                if isinstance(entry.value, MusicContext)
                else entry.value
            )
            if isinstance(value, MusicTrack):
                tracks[(entry.platform, entry.track_id)] = value
        rows = []
        for entry in sorted(entries, key=lambda row: row.created_at, reverse=True):
            if not isinstance(entry.value, MusicLyrics):
                continue
            track = tracks.get((entry.platform, entry.track_id))
            rows.append(
                {
                    "title": safe_text(track.title, 300) if track else "未知歌曲",
                    "artist": safe_text(track.artist, 300) if track else "未知歌手",
                    "source": entry.platform,
                    "input_source": entry.input_source,
                    "track_id": entry.track_id,
                    "cache_time": entry.created_at,
                    "expire_time": entry.expires_at,
                    **self._lyric_summary(entry.value),
                }
            )
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
        }

    def lyric_detail(self, source: str, track_id: str) -> dict | None:
        value = (
            self.cache.get_lyrics(source, track_id) if self.cache is not None else None
        )
        if value is None:
            return None
        data = self._lyric_summary(value)
        # This is a display preference, independent of cache/model budgets.
        cleaned = clean_lyrics(
            value.original, max_chars=len(value.original or ""),
            is_instrumental=value.is_instrumental,
        )
        full = self.state.settings.show_full_lyrics_preview
        # Redaction may expand a short match; full mode must not cut that result.
        sanitized = safe_text(cleaned.text, max(1, len(cleaned.text) * 2))
        data["preview"] = sanitized if full else sanitized[:400]
        data["preview_mode"] = "full" if full else "excerpt"
        data["preview_limit"] = None if full else 400
        data["preview_truncated"] = not full and len(sanitized) > 400
        data["source"] = value.source or source
        if not self.state.settings.debug_mode:
            return data
        raw_data = asdict(value)
        # Separate Debug display limit; never change stored raw Provider lyrics.
        for field in ("original", "translation"):
            raw = raw_data[field]
            raw_data[field] = raw[:64000] if raw is not None else None
        raw_data["display_truncated"] = any(
            isinstance(getattr(value, field), str)
            and len(getattr(value, field)) > 64000
            for field in ("original", "translation")
        )
        data["debug"] = {
            "raw_lyrics": raw_data["original"],
            "raw_translation": raw_data["translation"],
            "raw_lyrics_json": raw_data,
        }
        return data

    @staticmethod
    def _lyric_summary(value: MusicLyrics) -> dict:
        original = clean_lyrics(
            value.original, max_chars=len(value.original or ""), is_instrumental=value.is_instrumental
        )
        translation = clean_lyrics(
            value.translation, max_chars=len(value.translation or ""), is_instrumental=value.is_instrumental
        )
        return {
            "lyrics_status": (
                "instrumental" if value.is_instrumental
                else "available" if original.text else "unavailable"
            ),
            "translation_status": "available" if translation.text else "unavailable",
            "line_count": len(original.text.splitlines()),
            "character_count": len(original.text),
            "summary_truncated": original.truncated,
        }

    def contexts(self) -> list[dict]:
        return [
            {
                "id": row["id"],
                "generated_at": row["generated_at"],
                "song_info": row["context"]["track"],
                "metadata": {
                    "source": row["context"]["track"]["source"],
                    "has_lyrics": row["has_lyrics"],
                    "has_translation": row["has_translation"],
                },
                "summary": row["context"].get("song_summary")
                or row["context"].get("summary")
                or "歌曲信息与歌词数据；不包含用户意图或情绪判断",
                "summary_source": (
                    "processor" if row["context"].get("song_summary") else "provider"
                ),
                "retrieval": row["retrieval"],
            }
            for row in self.state.contexts()
        ]

    def context_detail(self, identifier: int) -> dict | None:
        row = next(
            (row for row in self.state.contexts() if row["id"] == identifier), None
        )
        return self._context_view(row) if row is not None else None

    def _context_view(self, row: dict) -> dict:
        context = row["context"]
        metadata = context.get("processor_metadata", {})
        result = {
            "id": row["id"],
            "generated_at": row["generated_at"],
            "basic_info": {
                key: context["track"].get(key)
                for key in ("title", "artist", "album", "source")
            },
            "analysis": {
                key: context.get(key)
                for key in ("song_summary", "artist_info", "emotion", "theme", "style", "keywords")
            },
            "processing": row["processing"],
            "sources": row["sources"],
            "quality": {
                "validation": metadata.get("validation"),
                "confidence": metadata.get("confidence"),
                "evidence": metadata.get("evidence"),
                "semantic_verified": False,
            },
            "injection": row["injection"],
            "injection_enabled": self.state.settings.enable_context_injection,
            "retrieval": row["retrieval"],
        }
        if self.state.settings.debug_mode:
            result["debug"] = row.get("debug") or {
                "full_music_context": context,
                "capture_status": "not_captured_enable_debug_before_request",
            }
        return result

    def processor_debug(self) -> dict:
        data = (
            dict(self.processor.last_status)
            if self.processor else {"status": "unavailable"}
        )
        if not self.state.settings.debug_mode:
            data.pop("processor_input", None)
            data.pop("processor_output", None)
        return data

    def history(self) -> dict:
        return {
            "enabled": self.state.replies_enabled,
            "limit": self.state.reply_limit,
            "persistent": self.state.persistent,
            "items": [
                {
                    **row,
                    "context": {"track": {
                        key: row["context"]["track"].get(key)
                        for key in ("title", "artist", "source")
                    }},
                }
                for row in self.state.history()
            ],
        }

    def clear_cache(self) -> bool:
        return self.cache.clear() if self.cache is not None else True

    def clear_history(self) -> bool:
        return self.state.clear_history()
