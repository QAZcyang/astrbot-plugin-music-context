"""Persist through the host config object; there is no dashboard config file."""

import asyncio
import copy
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict

from .config import PluginConfig, config_schema, schema_defaults


class ConfigConflictError(ValueError):
    """The editor must reload before overwriting newer settings."""


class ConfigService:
    def __init__(
        self,
        source: Mapping | None,
        *,
        on_change: Callable[[PluginConfig], None | Awaitable[None]] | None = None,
        model_options: Callable[[], list[dict[str, str]]] | None = None,
    ):
        self.source = source if source is not None else {}
        self.settings = PluginConfig.from_mapping(self.source)
        self.on_change = on_change
        self.model_options = model_options or (lambda: [])
        self._lock = asyncio.Lock()

    @property
    def writable(self) -> bool:
        return callable(getattr(self.source, "save_config", None)) or callable(
            getattr(self.source, "save_config_async", None)
        )

    def snapshot(self) -> dict:
        values = asdict(self.settings)
        revision = hashlib.sha256(
            json.dumps(values, sort_keys=True).encode()
        ).hexdigest()
        return {
            "values": values,
            "schema": config_schema(),
            "revision": revision,
            "writable": self.writable,
            "models": self.model_options(),
        }

    async def _apply(self, settings: PluginConfig) -> None:
        if self.on_change:
            result = self.on_change(settings)
            if inspect.isawaitable(result):
                await result

    async def update(self, values: dict, *, revision: str) -> dict:
        async with self._lock:
            if not isinstance(values, dict):
                raise TypeError("Configuration values must be an object")
            if not self.writable:
                raise OSError("Host configuration persistence unavailable")
            # Native config changes are observed before the next WebUI save.
            current = PluginConfig.from_mapping(self.source)
            if current != self.settings:
                self.settings = current
                await self._apply(current)
            if revision != self.snapshot()["revision"]:
                raise ConfigConflictError("Configuration changed; reload the editor")
            candidate = PluginConfig.from_mapping(values, strict=True)
            if candidate.processor_model and (
                candidate.processor_model != current.processor_model
                or candidate.enable_context_processor
            ) and candidate.processor_model not in {
                row["id"] for row in self.model_options()
            }:
                raise ValueError("Select an available AstrBot model")
            payload = asdict(candidate)
            original = copy.deepcopy(dict(self.source))

            async def persist():
                asynchronous = getattr(self.source, "save_config_async", None)
                if callable(asynchronous):
                    return await asynchronous(payload)
                await asyncio.to_thread(self.source.save_config, payload)
                return True

            write = asyncio.create_task(persist())
            cancelled = False
            try:
                # A disconnected browser must not leave disk and runtime divergent.
                while True:
                    try:
                        saved = await asyncio.shield(write)
                        break
                    except asyncio.CancelledError:
                        cancelled = True
                        if write.cancelled():
                            raise
            except Exception:
                # Host save may update its mapping before an atomic disk write fails.
                self.source.clear()
                self.source.update(original)
                raise
            if saved is False:
                self.settings = PluginConfig.from_mapping(self.source)
                await self._apply(self.settings)
                raise ConfigConflictError(
                    "Configuration save superseded; reload the editor"
                )
            self.settings = candidate
            await self._apply(candidate)
            if cancelled:
                raise asyncio.CancelledError
            return self.snapshot()

    async def reset(self, *, revision: str) -> dict:
        values = self.snapshot()["values"]
        values["enable_context_processor"] = False
        values["processor_model"] = ""
        values["context_processor"] = schema_defaults()["context_processor"]
        return await self.update(values, revision=revision)
