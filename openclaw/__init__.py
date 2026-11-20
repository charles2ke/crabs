"""Open Claw: watch Schengen visa appointment slots and alert on availability."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("openclaw")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0+unknown"

from .config import Config, ConfigError, load_config, parse_config
from .locking import FileLock, LockError
from .models import Alert, Slot, Watch
from .monitor import Monitor, SeenStore
from .notifiers import (
    ConsoleNotifier,
    DiscordNotifier,
    EmailNotifier,
    FileNotifier,
    Notifier,
    NotifierError,
    NtfyNotifier,
    PushoverNotifier,
    SlackNotifier,
    WebhookNotifier,
)
from .providers import (
    AuthenticationError,
    ChallengeError,
    Provider,
    ProviderError,
    get_provider,
    register_provider,
)

__all__ = [
    "Alert",
    "AuthenticationError",
    "ChallengeError",
    "Config",
    "ConfigError",
    "ConsoleNotifier",
    "DiscordNotifier",
    "EmailNotifier",
    "FileLock",
    "FileNotifier",
    "LockError",
    "Monitor",
    "Notifier",
    "NotifierError",
    "NtfyNotifier",
    "Provider",
    "ProviderError",
    "PushoverNotifier",
    "SeenStore",
    "SlackNotifier",
    "Slot",
    "Watch",
    "WebhookNotifier",
    "get_provider",
    "load_config",
    "parse_config",
    "register_provider",
]
