"""Adapt AstrBot messages to the independent text/card extractor."""

from collections.abc import Iterable, Mapping

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Json, Plain

from ..parser import CardMessageExtractor, MusicLinkCandidate
from ..parser.card_extractor import MAX_MESSAGE_PARTS
from ..parser.extractor import TextURLExtractor


class AstrBotMessageExtractor:
    """Preserve message order; unknown components are never stringified."""

    def __init__(self, *, enable_card_parser: bool = True) -> None:
        self.enable_card_parser = enable_card_parser

    def extract_urls(self, message: object) -> Iterable[str]:
        for candidate in self.extract_candidates(message):
            yield candidate.url

    def extract_candidates(self, message: object) -> Iterable[MusicLinkCandidate]:
        # 开关在宿主适配层、卡片解码之前生效，方便与其他卡片插件并用；
        # 关闭后仅保留显式文本链接，不从 JSON/XML 内容或投影文本恢复卡片输入。
        if not self.enable_card_parser:
            yield from self._text_candidates(message)
            return
        extractor = CardMessageExtractor()
        if not isinstance(message, AstrMessageEvent):
            yield from extractor.extract_candidates(message)
            return

        # AstrBot currently has Json but no Xml component. Its OneBot adapter
        # retains the raw event even when an unsupported XML segment is dropped.
        # Use that complete segment array when cards occur to keep source order
        # and avoid extracting the same JSON through both raw and cooked chains.
        # 原始 OneBot 段补足组件链可能丢失的 XML；整条消息只选一种表示，保持顺序并避免重复。
        message_obj = getattr(message, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None)
        segments = raw.get("message") if isinstance(raw, Mapping) else None
        if isinstance(segments, list) and any(
            isinstance(part, Mapping) and part.get("type") in ("json", "xml")
            for part in segments[:MAX_MESSAGE_PARTS]
        ):
            yield from extractor.extract_candidates(segments)
            return

        components = message.get_messages() or []
        if not components:
            yield from extractor.extract_candidates(message.get_message_str())
            return
        for part in components[:MAX_MESSAGE_PARTS]:
            if isinstance(part, Plain):
                yield from extractor.extract_candidates(part.text)
            elif isinstance(part, Json):
                # Json 的 data 已由 AstrBot 解码，但 message_str 不包含它。
                # 提取层将卡片导航/提示转换为统一 Candidate，歌曲身份仍由
                # MusicParser 校验；Provider 不接收聊天平台原始消息。
                yield from extractor.extract_json(part.data)

    def _text_candidates(self, message: object) -> Iterable[MusicLinkCandidate]:
        """Skip card segments before decoding; keep explicit text links."""
        extractor = TextURLExtractor()
        if not isinstance(message, AstrMessageEvent):
            if isinstance(message, str):
                yield from extractor.extract_candidates(message)
            return
        message_obj = getattr(message, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None)
        segments = raw.get("message") if isinstance(raw, Mapping) else None
        if isinstance(segments, list) and any(
            isinstance(part, Mapping) and part.get("type") in ("json", "xml")
            for part in segments[:MAX_MESSAGE_PARTS]
        ):
            for part in segments[:MAX_MESSAGE_PARTS]:
                if isinstance(part, Mapping) and part.get("type") == "text":
                    data = part.get("data")
                    text = data.get("text") if isinstance(data, Mapping) else None
                    if isinstance(text, str):
                        yield from extractor.extract_candidates(text)
            return
        components = message.get_messages() or []
        if not components:
            yield from extractor.extract_candidates(message.get_message_str())
            return
        for part in components[:MAX_MESSAGE_PARTS]:
            if isinstance(part, Plain):
                yield from extractor.extract_candidates(part.text)
