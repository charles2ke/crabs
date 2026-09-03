"""Slot providers.

A provider knows how to turn a :class:`~openclaw.models.Watch` into the list of
currently available :class:`~openclaw.models.Slot` objects.
"""

from __future__ import annotations

from .base import AuthenticationError, Provider, ProviderError, get_provider, register_provider
from .bls_international import BlsInternationalProvider
from .http_json import HttpJsonProvider
from .mock import MockProvider
from .tlscontact import TlscontactProvider
from .vfs_global import VfsGlobalProvider

__all__ = [
    "BlsInternationalProvider",
    "HttpJsonProvider",
    "MockProvider",
    "TlscontactProvider",
    "VfsGlobalProvider",
    "AuthenticationError",
    "Provider",
    "ProviderError",
    "get_provider",
    "register_provider",
]
