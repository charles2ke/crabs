"""Provider registry and base class."""

from __future__ import annotations

import abc
from typing import Callable

from ..models import Slot, Watch


class ProviderError(RuntimeError):
    """Raised when a provider cannot report slot availability."""


class AuthenticationError(ProviderError):
    """Raised when provider authentication fails or a session expires."""


class ChallengeError(AuthenticationError):
    """Raised when a portal answers with a CAPTCHA, anti-bot, or WAF challenge.

    Open Claw never solves or bypasses such challenges. The error exists so the
    monitor can report a distinct provider-health condition and a human can
    decide how to proceed.
    """


class Provider(abc.ABC):
    """Base class for slot providers."""

    name: str = "base"

    @abc.abstractmethod
    def fetch(self, watch: Watch) -> list[Slot]:
        """Return every currently available slot for ``watch``.

        Implementations should raise :class:`ProviderError` for recoverable
        failures (network problems, unexpected payloads) so the monitor can keep
        polling instead of crashing.
        """


_REGISTRY: dict[str, Callable[[], Provider]] = {}


def register_provider(name: str, factory: Callable[[], Provider]) -> None:
    """Register ``factory`` under ``name`` (case-insensitive)."""
    _REGISTRY[name.lower()] = factory


def get_provider(name: str) -> Provider:
    """Instantiate the provider registered under ``name``."""
    try:
        factory = _REGISTRY[name.lower()]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise ProviderError(
            f"unknown provider {name!r}; registered providers: {known}"
        ) from None
    return factory()
