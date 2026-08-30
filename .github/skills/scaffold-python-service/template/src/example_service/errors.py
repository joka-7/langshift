"""Custom exception hierarchy, rooted at one base class per the Python rules."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for every exception this service raises deliberately.

    Catch this at the framework boundary to distinguish expected failures (bad
    input, missing resource) from genuine bugs, which should propagate and crash
    loudly rather than being swallowed here.
    """


class NotFoundError(ServiceError):
    """A requested resource does not exist."""

    def __init__(self, resource: str, identifier: str) -> None:
        self.resource = resource
        self.identifier = identifier
        super().__init__(f"{resource} not found: {identifier}")


class ValidationError(ServiceError):
    """Input failed a business-rule check the framework's own validation doesn't cover."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"invalid {field}: {reason}")
