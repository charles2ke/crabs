"""Provider registry and base class."""

from __future__ import annotations

import abc
from typing import Callable

from ..models import Slot, Watch


class ProviderError(RuntimeError):
    """Raised when a provider cannot report slot availability."""


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
