"""Optional bounded preprocessing; failed inference returns the input untouched."""

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime

from astrbot.api import logger

from ..config import PluginConfig
from ..lyrics import clean_lyrics
from ..models import MusicContext, MusicLyrics, MusicTrack
from ..models.context import ProcessorMetadata
from .processor_prompt import PROCESSOR_PROMPT, PROMPT_VERSION
from .processor_transport import AstrBotModelAdapter, ModelCompletion, ProcessorError

TEXT_LIMITS = {"song_summary": 240, "artist_info": 160}
TAG_LIMITS = {key: (6, 32) for key in ("style", "emotion", "theme", "keywords")}


def parse_enhancement(text: str) -> dict:
    """Accept exactly six bounded JSON fields, repairing only wrapper text."""
    if not isinstance(text, str) or len(text) > 16000:
        raise ProcessorError("invalid_json")
    start, end = text.find("{"), text.rfind("}")
    try:
        value = json.loads(text[start : end + 1])
        if not isinstance(value, dict) or set(value) != set(TEXT_LIMITS) | set(TAG_LIMITS):
            raise ValueError
        for key, size in TEXT_LIMITS.items():
            if not isinstance(value[key], str) or len(value[key]) > size:
                raise ValueError
        for key, (count, size) in TAG_LIMITS.items():
            if (
                not isinstance(value[key], list)
                or len(value[key]) > count
                or any(
                    not isinstance(item, str) or not item.strip() or len(item) > size
                    for item in value[key]
                )
            ):
                raise ValueError
            value[key] = tuple(dict.fromkeys(value[key]))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ProcessorError("invalid_json") from None
    return value


class ContextProcessor:
    # 可选、默认关闭的歌曲文本解释层；主对话仍由 AstrBot 发起。
    # 并发忙碌或模型失败时返回输入事实 Context，不阻塞或接管普通聊天。
    def __init__(
        self, config: PluginConfig, *, transport: ModelCompletion | None = None
    ) -> None:
        self.config = config
        self.transport = transport or AstrBotModelAdapter()
        self._lock = asyncio.Lock()
        self.last_status = {
            "status": "idle" if config.enable_context_processor else "disabled",
            "model": config.processor_model,
            "version": PROMPT_VERSION,
        }

    @staticmethod
    def _input(context: MusicContext, budget: int) -> dict:
        lyrics = clean_lyrics(
            context.lyrics.original,
            max_chars=budget,
            is_instrumental=context.lyrics.is_instrumental,
        )
        return {
            "title": context.track.title[:300],
            "artist": context.track.artist[:300],
            "album": (context.track.album or "")[:300],
            "cleaned_lyrics": lyrics.text,
            "instrumental": context.lyrics.is_instrumental,
            "input_truncated": lyrics.truncated,
        }

    async def process_music_context(
        self, context: MusicContext, *, diagnostics: dict | None = None
    ) -> MusicContext:
        if not self.config.enable_context_processor:
            self.last_status = {
                "status": "disabled", "model": self.config.processor_model,
                "version": PROMPT_VERSION,
            }
            if diagnostics is not None:
                diagnostics.update(self.last_status)
            return context
        return await self._process(context, diagnostics=diagnostics)

    async def _process(
        self, context: MusicContext, *, probe: bool = False, diagnostics: dict | None = None
    ) -> MusicContext:
        if self._lock.locked():
            if diagnostics is not None:
                diagnostics.update(status="busy", model=self.config.processor_model)
            logger.warning("Music processor skipped (busy)")
            return context
        async with self._lock:
            started = time.monotonic()
            status = diagnostics if diagnostics is not None else {}
            status.update({
                "status": "running",
                "model": self.config.processor_model,
                "version": PROMPT_VERSION,
                "processed_at": datetime.now(UTC).isoformat(),
                "kind": "connection_test" if probe else "music",
                "input_length": 0,
                "output_fields": [],
                "token_estimate": 0,
                "attempts": 0,
            })
            if self.config.debug_mode:
                status.update(processor_input=[], processor_output=[])
            self.last_status = status
            try:
                async with asyncio.timeout(self.config.context_processor.timeout):
                    # 最多两次生成共用总超时，避免结构修复重试无限延长本轮聊天等待。
                    budget = 8000
                    for attempt in range(2):
                        data = self._input(context, budget)
                        prompt = PROCESSOR_PROMPT + (
                            "\nPrevious attempt failed validation. Use short values and return valid JSON exactly as specified."
                            if attempt
                            else ""
                        )
                        serialized = json.dumps(data, ensure_ascii=False)
                        status.update(
                            input_length=len(serialized),
                            token_estimate=len(prompt) + len(serialized),
                            input_truncated=data["input_truncated"],
                            attempts=attempt + 1,
                        )
                        try:
                            messages = [
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": serialized},
                            ]
                            if self.config.debug_mode:
                                status["processor_input"].append(messages)
                            response = await self.transport.complete(
                                self.config, messages,
                            )
                            if self.config.debug_mode:
                                status["processor_output"].append(response.text[:16000])
                            enhancement = parse_enhancement(response.text)
                            if not data["cleaned_lyrics"] or data["instrumental"]:
                                enhancement.update({key: () for key in TAG_LIMITS})
                                # Metadata-only results must not invent listening or lyric claims.
                                enhancement["song_summary"] = (
                                    "仅有歌曲元数据；无可分析歌词。"
                                )
                            status.update(
                                status="success", output_fields=list(enhancement)
                            )
                            return replace(
                                context,
                                summary=enhancement["song_summary"],
                                artist_info=enhancement["artist_info"],
                                style_tags=enhancement["style"],
                                emotion_tags=enhancement["emotion"],
                                theme_tags=enhancement["theme"],
                                lyrics_topics=enhancement["keywords"],
                                processor_metadata=ProcessorMetadata(
                                    # schema_valid 仅表示六字段结构合格；未校准语义评估，置信度保持 None。
                                    enabled=True,
                                    model=response.model,
                                    processed_at=status["processed_at"],
                                    version=PROMPT_VERSION,
                                    generated_by="context_processor",
                                    confidence=None,
                                    validation="schema_valid",
                                    evidence=(
                                        "metadata_only"
                                        if not data["cleaned_lyrics"] or data["instrumental"]
                                        else "lyrics_text"
                                    ),
                                ),
                            )
                        except ProcessorError as exc:
                            if attempt or exc.code not in (
                                "invalid_json",
                                "invalid_response",
                                "input_limit",
                                "output_limit",
                            ):
                                raise
                            if exc.code == "input_limit":
                                budget //= 2
                            status["retry_reason"] = exc.code
                    raise ProcessorError("invalid_json")
            except asyncio.CancelledError:
                status.update(status="cancelled", error="cancelled")
                raise
            except TimeoutError:
                status.update(status="failed", error="timeout")
            except ProcessorError as exc:
                status.update(status="failed", error=exc.code)
            except Exception:  # noqa: BLE001 - optional inference must fail open
                status.update(status="failed", error="processor_error")
            finally:
                status["latency_ms"] = round((time.monotonic() - started) * 1000)
                self.last_status = dict(status)
            logger.warning(
                "Music processor skipped (%s)", status.get("error", "cancelled")
            )
            return context

    async def test_connection(self) -> dict:
        if not self.config.enable_context_processor:
            self.last_status = {
                "status": "disabled", "model": self.config.processor_model,
                "version": PROMPT_VERSION,
            }
            return {
                "success": False, "model": self.config.processor_model,
                "latency_ms": 0, "error": "disabled",
            }
        if self._lock.locked():
            return {
                "success": False,
                "model": self.config.processor_model,
                "latency_ms": 0,
                "error": "busy",
            }
        fixed = MusicContext(
            MusicTrack(
                title="Connection test", artist="Test", source="test", track_id="test"
            ),
            MusicLyrics(),
        )
        result = await self._process(fixed, probe=True)
        return {
            "success": result.processor_metadata.enabled,
            "model": self.config.processor_model,
            "latency_ms": self.last_status.get("latency_ms", 0),
            "error": self.last_status.get("error"),
        }
