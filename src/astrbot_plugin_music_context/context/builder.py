"""Build factual music data without choosing an LLM injection mechanism."""

import json
from dataclasses import replace

from ..lyrics import clean_lyrics
from ..lyrics.translation import chinese_translation
from ..models import MusicContext, MusicLyrics, MusicTrack
from ..models.context import LyricsPresentation


def _json(value: object) -> str:
    """Use one JSON format for budget accounting and exported data."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _fit_text(text: str, available_chars: int) -> str | None:
    """Fit complete prefix lines into the space replacing a JSON null value."""
    # 预算按实际 JSON 转义后的字符数计算，保留完整前缀行，避免截断歌词行或漏算换行。
    lines: list[str] = []
    # Replacing null (4 characters) with string quotes (2) frees two characters.
    content_budget = available_chars + 2
    length = 0
    for line in text.splitlines():
        extra_length = len(_json(line)) - 2 + (2 if lines else 0)
        if length + extra_length > content_budget:
            break
        lines.append(line)
        length += extra_length
    return "\n".join(lines) or None


class MusicContextBuilder:
    """Combine existing models, clean lyrics, and bound serialized data size.

    The default 8000-character budget includes metadata, both lyric streams,
    JSON keys, separators, and escaped characters. It is a size bound, not an
    exact token count; integration must account for its model and full prompt.
    Original lyrics take priority over translation within the shared budget.
    Lyrics may be incomplete; source objects always retain their full text.

    No background knowledge, user intent, emotion, reply, or instructions are
    generated. Metadata remains in MusicTrack without a duplicated schema.
    """

    # Builder 是纯数据转换：输入来自 Provider/缓存的统一模型，不检索歌曲、不调用模型。
    # 原始歌词留在源对象，输出只清洗并限长；原歌词优先于翻译使用共同预算。
    def __init__(
        self, *, max_context_chars: int = 8000, lyrics_mode: str = "bilingual",
        original_lyrics_chars: int = 6000, translation_extra_chars: int = 2000,
    ) -> None:
        if type(max_context_chars) is not int or max_context_chars < 0:
            raise ValueError("max_context_chars must be nonnegative")
        if lyrics_mode not in ("original", "bilingual"):
            raise ValueError("Invalid lyrics mode")
        if type(original_lyrics_chars) is not int or not 1 <= original_lyrics_chars <= 32000:
            raise ValueError("Invalid original lyrics budget")
        if type(translation_extra_chars) is not int or not 0 <= translation_extra_chars <= 16000:
            raise ValueError("Invalid translation budget")
        self.max_context_chars = max_context_chars
        self.lyrics_mode = lyrics_mode
        self.original_lyrics_chars = original_lyrics_chars
        self.translation_extra_chars = translation_extra_chars

    @property
    def policy(self) -> str:
        return f"m046-v1:{self.lyrics_mode}:{self.original_lyrics_chars}:{self.translation_extra_chars}"

    def build(
        self, track: MusicTrack | None, lyrics: MusicLyrics | None = None,
    ) -> MusicContext | None:
        if track is None:
            return None
        raw = lyrics if lyrics is not None else MusicLyrics()
        return self._budget(MusicContext(track=track, lyrics=raw, original_lyrics=raw.original))

    def _budget(self, context: MusicContext, *, admitted_translation: bool = False) -> MusicContext | None:
        raw = context.lyrics
        original = clean_lyrics(raw.original, max_chars=len(raw.original or ""),
                                is_instrumental=raw.is_instrumental).text
        translation, reason = (
            (raw.translation or "", context.lyrics_presentation.fallback_reason)
            if admitted_translation else chinese_translation(raw)
        )
        if raw.is_instrumental:
            translation, reason = "", "instrumental"
        elif not original:
            translation, reason = "", "original_missing"
        if self.lyrics_mode == "original":
            translation, reason = "", None
        empty = replace(context, lyrics=replace(raw, original=None, translation=None))
        # Reserve identical worst-case status overhead in both modes: translation
        # never steals the original allocation, including fallback/flag changes.
        reserve = LyricsPresentation("bilingual", "bilingual",
                                     "translation_language_unconfirmed", False, False, "netease")
        remaining = self.max_context_chars - len(self.to_json(replace(empty, lyrics_presentation=reserve)))
        if remaining < 0:
            # Metadata may still fit without any lyrics, using accurate status.
            result = replace(empty, lyrics_presentation=LyricsPresentation(
                self.lyrics_mode, "original", "context_budget",
                original_truncated=bool(original), translation_truncated=bool(translation)))
            return result if len(self.to_json(result)) <= self.max_context_chars else None
        bounded = clean_lyrics(original, max_chars=self.original_lyrics_chars).text
        fitted_original = _fit_text(bounded, remaining)
        if fitted_original is not None:
            remaining -= len(_json(fitted_original)) - len("null")
        bounded_translation = clean_lyrics(translation, max_chars=self.translation_extra_chars).text
        fitted_translation = _fit_text(bounded_translation, remaining) if fitted_original else None
        if translation and not fitted_translation:
            reason = "translation_budget"
        presentation = LyricsPresentation(
            requested_mode=self.lyrics_mode,
            effective_mode="bilingual" if fitted_translation else "original",
            fallback_reason=reason,
            original_truncated=bool(original and fitted_original != original),
            translation_truncated=bool(translation and fitted_translation != translation),
            translation_source="netease" if fitted_translation else None,
        )
        return replace(empty, lyrics=replace(raw, original=fitted_original,
                       translation=fitted_translation,
                       translation_language="zh" if fitted_translation else None),
                       lyrics_presentation=presentation)

    @staticmethod
    def prepare(context: MusicContext) -> MusicContext:
        """Cleaner output for optional processing; raw text remains a separate field."""
        # 可选模型处理先使用独立清洗输入，随后 finalize 按注入预算收敛，避免分析只看到短截取。
        original = (
            context.original_lyrics
            if context.original_lyrics is not None
            else context.lyrics.original
        )
        cleaned = clean_lyrics(
            original, max_chars=64000, is_instrumental=context.lyrics.is_instrumental
        )
        return replace(
            context, lyrics=replace(context.lyrics, original=cleaned.text or None)
        )

    def finalize(
        self, context: MusicContext, *, fallback: MusicContext
    ) -> MusicContext:
        """Fit optional inference into the existing budget, preserving source objects."""
        # 辅助解释不能挤掉基本事实：增强字段本身超预算时，回退到先前有界事实 Context。
        if not context.processor_metadata.enabled:
            return fallback
        # Bind only inference to this request's factual streams. Never reuse the
        # cached result's mode or lyrics (metadata-only results can cross modes).
        rebound = replace(context, track=fallback.track, lyrics=fallback.lyrics,
                          original_lyrics=fallback.original_lyrics,
                          lyrics_presentation=fallback.lyrics_presentation)
        final = self._budget(rebound, admitted_translation=True)
        if final is None or final.lyrics.original != fallback.lyrics.original:
            return fallback
        # Earlier truncation cannot be undone by fitting an already bounded stream.
        return replace(final, lyrics_presentation=replace(final.lyrics_presentation,
            original_truncated=fallback.lyrics_presentation.original_truncated,
            translation_truncated=(fallback.lyrics_presentation.translation_truncated
                                   or final.lyrics_presentation.translation_truncated),
            fallback_reason=(final.lyrics_presentation.fallback_reason
                             if fallback.lyrics.translation else fallback.lyrics_presentation.fallback_reason)))

    @staticmethod
    def to_json(context: MusicContext) -> str:
        """Serialize track and lyrics as data; source text stays JSON-escaped.

        The build budget applies to this compact representation. Other output
        formats and surrounding prompts need their own integration accounting.
        """
        return _json(context.payload())
