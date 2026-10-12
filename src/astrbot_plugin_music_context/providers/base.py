"""Provider lifecycle and compatibility with ID-based retrieval APIs."""

from ..models import MusicLyrics, MusicTrack
from ..parser import MusicLinkCandidate, MusicLinkResult
from .capability import DATA_TYPES, INPUT_TYPES, MusicResource, ProviderCapability

MusicCandidate = MusicLinkCandidate | MusicLinkResult


class MusicProvider:
    """Retrieve raw resources independently of cache and AstrBot.

    Providers may implement fetch directly or retain the two ID-based methods.
    URL recognition is a local adapter consumed by MusicParser; it must not
    perform network I/O. Card hints are never authoritative song data.
    """

    # Provider 只获取歌曲信息/原始歌词并返回统一模型，不负责缓存、Context 或 LLM。
    # 新平台沿用此契约、声明能力并注册即可接入现有流程，无需在集成层增加平台分支。
    name: str = ""
    priority: int = 0

    @property
    def capabilities(self) -> ProviderCapability:
        """Compatibility declaration for M-016 Providers; override to restrict."""
        return ProviderCapability(
            platforms=frozenset({self.name}),
            data_types=DATA_TYPES,
            input_types=INPUT_TYPES,
            priority=self.priority,
        )

    @classmethod
    def create_default(cls) -> "MusicProvider":
        """Construct the instance used by package discovery, without I/O."""
        return cls()

    def parse_url(self, url: str) -> MusicLinkResult | None:
        """Optional platform URL adapter registered with the existing Parser."""
        return None

    def supports(self, candidate: MusicCandidate) -> bool:
        """Match a resolved platform or locally recognize a raw URL candidate."""
        link = self._identity(candidate)
        return (
            isinstance(link, MusicLinkResult)
            and link.platform in self.capabilities.platforms
        )

    def _identity(self, candidate: MusicCandidate) -> MusicLinkResult | None:
        if isinstance(candidate, MusicLinkResult):
            return candidate
        if candidate.url:
            return self.parse_url(candidate.url)
        if candidate.platform and candidate.track_id:
            return MusicLinkResult(candidate.platform, candidate.track_id, "")
        return None

    async def fetch(
        self, candidate: MusicCandidate, *, resource: MusicResource = "track"
    ) -> MusicTrack | MusicLyrics:
        """Fetch metadata by default, or raw lyrics, for a resolved candidate.

        Separate resources preserve partial cache hits and metadata on lyrics
        failure/timeout. Cancellation and errors propagate to the coordinator.
        """
        # track/lyrics 分开获取，才能复用部分缓存；歌词失败也不会丢掉已取得的歌曲信息。
        # 可直接实现 fetch，或保留 fetch_metadata/fetch_lyrics 这对 ID 接口。
        link = self._identity(candidate)
        if not isinstance(link, MusicLinkResult) or not self.supports(link):
            raise ValueError("Provider does not support the candidate")
        if resource == "track":
            return await self.fetch_metadata(link.track_id)
        if resource == "lyrics":
            return await self.fetch_lyrics(link.track_id)
        raise ValueError("Unknown music resource")

    async def fetch_metadata(self, track_id: str) -> MusicTrack:
        """Legacy metadata API; providers implementing fetch need not override."""
        raise NotImplementedError

    async def fetch_lyrics(self, track_id: str) -> MusicLyrics:
        """Legacy lyrics API; providers implementing fetch need not override."""
        raise NotImplementedError
