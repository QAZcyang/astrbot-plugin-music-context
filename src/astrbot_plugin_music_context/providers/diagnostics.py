"""Request-local optional response observation; never changes Provider results."""

import json
from contextvars import ContextVar

provider_responses: ContextVar[list[dict] | None] = ContextVar(
    "music_provider_responses", default=None
)


def observe_response(resource: str, payload: object) -> None:
    """Capture at most two bounded JSON responses while Debug is enabled."""
    target = provider_responses.get()
    if target is None or len(target) >= 2:
        return
    try:
        serialized = json.dumps(payload, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > 256 * 1024:
            target.append({"resource": resource, "unavailable": "response_size_limit"})
        else:
            target.append({"resource": resource, "response": json.loads(serialized)})
    except (ValueError, TypeError, UnicodeError, RecursionError):
        target.append({"resource": resource, "unavailable": "not_json"})
