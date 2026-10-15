"""One schema supplies defaults, validation and both AstrBot/WebUI editors."""

import copy
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from astrbot.api import logger

_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "_conf_schema.json").read_text(
        encoding="utf-8"
    )
)


def config_schema() -> dict:
    return copy.deepcopy(_SCHEMA)


def schema_defaults(schema: dict | None = None) -> dict:
    return {
        name: schema_defaults(item["items"])
        if item["type"] == "object"
        else item["default"]
        for name, item in (schema if schema is not None else _SCHEMA).items()
    }


def validate_fields(values: Mapping, schema: dict, *, strict: bool = False) -> dict:
    if not isinstance(values, Mapping) or (strict and set(values) - set(schema)):
        raise ValueError("Unknown configuration fields or invalid object")
    result = schema_defaults(schema)
    for name, item in schema.items():
        value = values.get(name, result[name])
        # M-046: tolerate an invalid mode without disabling all music handling.
        if name == "lyrics_mode" and value not in ("bilingual", "original"):
            logger.warning("Invalid lyrics mode; using bilingual")
            value = item["default"]
        if name in ("original_lyrics_chars", "translation_extra_chars") and not strict:
            if type(value) is not int or not item["minimum"] <= value <= item["maximum"]:
                logger.warning("Invalid lyrics budget; using default")
                value = item["default"]
        kind = item["type"]
        if kind == "object":
            result[name] = validate_fields(value, item["items"], strict=strict)
            continue
        valid = type(value) is {"bool": bool, "int": int, "string": str}.get(kind)
        if kind == "float":
            valid = type(value) in (float, int) and math.isfinite(value)
        if not valid:
            raise ValueError(f"Invalid configuration field: {name}")
        if kind in ("float", "int") and not item["minimum"] <= value <= item["maximum"]:
            raise ValueError(f"Invalid configuration range: {name}")
        if kind == "string" and (
            len(value) > item.get("max_length", 256) or any(ord(c) < 32 for c in value)
        ):
            raise ValueError(f"Invalid configuration text: {name}")
        if "options" in item and value not in item["options"]:
            raise ValueError(f"Invalid configuration option: {name}")
        result[name] = float(value) if kind == "float" else value
    return result


_DEFAULT = schema_defaults()
_PROCESSOR = _DEFAULT["context_processor"]


@dataclass(frozen=True, slots=True)
class ProcessorConfig:
    temperature: float = _PROCESSOR["temperature"]
    max_tokens: int = _PROCESSOR["max_tokens"]
    timeout: int = _PROCESSOR["timeout"]


@dataclass(frozen=True, slots=True)
class PluginConfig:
    lyrics_mode: str = _DEFAULT["lyrics_mode"]
    original_lyrics_chars: int = _DEFAULT["original_lyrics_chars"]
    translation_extra_chars: int = _DEFAULT["translation_extra_chars"]
    enable_cache: bool = _DEFAULT["enable_cache"]
    cache_ttl: int = _DEFAULT["cache_ttl"]
    max_cache_items: int = _DEFAULT["max_cache_items"]
    show_full_lyrics_preview: bool = _DEFAULT["show_full_lyrics_preview"]
    enable_context_injection: bool = _DEFAULT["enable_context_injection"]
    max_context_length: int = _DEFAULT["max_context_length"]
    debug_mode: bool = _DEFAULT["debug_mode"]
    history_enable: bool = _DEFAULT["history_enable"]
    max_history_items: int = _DEFAULT["max_history_items"]
    store_llm_history: bool = _DEFAULT["store_llm_history"]
    llm_history_limit: int = _DEFAULT["llm_history_limit"]
    enable_context_processor: bool = _DEFAULT["enable_context_processor"]
    processor_model: str = _DEFAULT["processor_model"]
    context_processor: ProcessorConfig = field(default_factory=ProcessorConfig)
    enable_card_parser: bool = _DEFAULT["enable_card_parser"]

    @classmethod
    def from_mapping(
        cls, values: Mapping | None = None, *, strict: bool = False
    ) -> "PluginConfig":
        result = validate_fields(
            {} if values is None else values, _SCHEMA, strict=strict
        )
        processor = result.pop("context_processor")
        return cls(**result, context_processor=ProcessorConfig(**processor))
