"""Build factual music data without choosing an LLM injection mechanism."""

import json
from dataclasses import replace

from ..lyrics import clean_lyrics
from ..models import MusicContext, MusicLyrics, MusicTrack


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
    def __init__(self, *, max_context_chars: int = 8000) -> None:
        if max_context_chars < 0:
            raise ValueError("max_context_chars must be nonnegative")
        self.max_context_chars = max_context_chars

    def build(
        self,
        track: MusicTrack | None,
        lyrics: MusicLyrics | None = None,
    ) -> MusicContext | None:
        """Return bounded context or None when no track/metadata space exists.

        Missing lyrics remain unknown, rather than implying instrumental music.
        Supplied metadata, language tags, source, and instrumental status are
        preserved. Negative budget configuration is rejected at construction.
        """
        if track is None:
            return None
        raw_lyrics = lyrics if lyrics is not None else MusicLyrics()
        cleaned_lyrics = replace(raw_lyrics, original=None, translation=None)
        context = MusicContext(track=track, lyrics=cleaned_lyrics)
        remaining = self.max_context_chars - len(self.to_json(context))
        if remaining < 0:
            return None

        for field in ("original", "translation"):
            cleaned = clean_lyrics(
                getattr(raw_lyrics, field),
                max_chars=self.max_context_chars,
                is_instrumental=raw_lyrics.is_instrumental,
            )
            fitted = _fit_text(cleaned.text, remaining)
            if fitted is not None:
                remaining -= len(_json(fitted)) - len("null")
            cleaned_lyrics = replace(cleaned_lyrics, **{field: fitted})

        return MusicContext(
            track=track, lyrics=cleaned_lyrics, original_lyrics=raw_lyrics.original
        )

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
        empty = replace(
            context, lyrics=replace(context.lyrics, original=None, translation=None)
        )
        remaining = self.max_context_chars - len(self.to_json(empty))
        if remaining < 0:
            return fallback
        lyrics = empty.lyrics
        for name in ("original", "translation"):
            text = getattr(context.lyrics, name) or ""
            fitted = _fit_text(text, remaining)
            if fitted is not None:
                remaining -= len(_json(fitted)) - len("null")
            lyrics = replace(lyrics, **{name: fitted})
        return replace(empty, lyrics=lyrics)

    @staticmethod
    def to_json(context: MusicContext) -> str:
        """Serialize track and lyrics as data; source text stays JSON-escaped.

        The build budget applies to this compact representation. Other output
        formats and surrounding prompts need their own integration accounting.
        """
        return _json(context.payload())
