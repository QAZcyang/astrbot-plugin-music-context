"""Delegate model connections and authentication entirely to AstrBot."""

from dataclasses import dataclass
from typing import Protocol

from ..config import PluginConfig


class ProcessorError(Exception):
    """Stable safe diagnostic code without upstream response bodies."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str


class ModelCompletion(Protocol):
    async def complete(
        self, config: PluginConfig, messages: list[dict[str, str]]
    ) -> Completion: ...


class ModelHost(Protocol):
    def get_all_providers(self) -> list: ...
    def get_provider_by_id(self, provider_id: str) -> object | None: ...


class AstrBotModelAdapter:
    """Use only loaded chat providers; never read connection or secret settings."""

    def __init__(self, context: ModelHost | None = None) -> None:
        self.context = context

    def models(self) -> list[dict[str, str]]:
        if self.context is None:
            return []
        try:
            providers = self.context.get_all_providers()
        except Exception:
            return []
        result = {}
        for provider in providers:
            try:
                meta = provider.meta()
                if not callable(getattr(provider, "text_chat", None)):
                    continue
                if not isinstance(meta.id, str) or not 0 < len(meta.id) <= 200:
                    continue
                if any(ord(c) < 32 for c in meta.id):
                    continue
                model = meta.model if isinstance(meta.model, str) else ""
                result[meta.id] = {
                    "id": meta.id,
                    "label": meta.id + (" · " + model[:200] if model else ""),
                }
            except Exception:
                continue
        return list(result.values())

    async def complete(
        self, config: PluginConfig, messages: list[dict[str, str]]
    ) -> Completion:
        if not config.enable_context_processor:
            raise ProcessorError("disabled")
        if not config.processor_model:
            raise ProcessorError("not_configured")
        if config.processor_model not in {row["id"] for row in self.models()}:
            raise ProcessorError("model_not_found")
        try:
            provider = self.context.get_provider_by_id(config.processor_model)
            if provider is None or not callable(getattr(provider, "text_chat", None)):
                raise ProcessorError("model_not_found")
            # No persona, conversation history, tools, session or model override.
            # 使用已配置的 AstrBot Provider 与独立空历史，连接/认证归宿主管理；
            # 预处理不会替换主对话模型或把用户对话历史发给辅助模型。
            response = await provider.text_chat(
                prompt=messages[1]["content"],
                system_prompt=messages[0]["content"],
                contexts=[],
                temperature=config.context_processor.temperature,
                max_tokens=config.context_processor.max_tokens,
            )
            text = response.completion_text
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ProcessorError("invalid_response")
            return Completion(text, config.processor_model)
        except ProcessorError:
            raise
        except Exception:
            raise ProcessorError("model_call_failed") from None
