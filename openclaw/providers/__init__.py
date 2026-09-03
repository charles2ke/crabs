"""Slot providers.

A provider knows how to turn a :class:`~openclaw.models.Watch` into the list of
currently available :class:`~openclaw.models.Slot` objects.
"""

from __future__ import annotations

from .base import AuthenticationError, Provider, ProviderError, get_provider, register_provider
from .http_json import HttpJsonProvider
from .mock import MockProvider

__all__ = [
    "HttpJsonProvider",
    "MockProvider",
    "AuthenticationError",
    "Provider",
    "ProviderError",
    "get_provider",
    "register_provider",
]
