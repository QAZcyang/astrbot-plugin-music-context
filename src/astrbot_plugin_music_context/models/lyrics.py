"""Lyrics data without retrieval, cleaning, or language inference."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MusicLyrics:
    """Original and translated text with optional source and language tags.

    Language tags are supplied by the caller, for example ``en`` or ``zh``.
    Missing lyrics do not imply instrumental music; that flag must be explicit.
    """

    original: str | None = None
    translation: str | None = None
    source: str | None = None
    is_instrumental: bool = False
    original_language: str | None = None
    translation_language: str | None = None

    @property
    def has_lyrics(self) -> bool:
        """Whether either stored text is nonempty, without parsing its contents."""
        return bool(self.original or self.translation)
