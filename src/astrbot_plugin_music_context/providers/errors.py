"""Stable routing/execution errors with log-safe messages."""


class ProviderRoutingError(Exception):
    """Base error; messages never include candidate or upstream text."""

    code = "provider_routing_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class NoMatchingProviderError(ProviderRoutingError, LookupError):
    code = "no_matching_provider"


class InsufficientCapabilityError(ProviderRoutingError, ValueError):
    code = "insufficient_provider_capability"


class ProviderExecutionError(ProviderRoutingError):
    code = "provider_execution_failed"

    def __init__(self, *, stage: str, cause_type: str) -> None:
        super().__init__()
        self.stage = stage
        self.cause_type = cause_type
