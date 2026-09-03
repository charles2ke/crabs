"""Open Claw: watch Schengen visa appointment slots and alert on availability."""

from .config import Config, ConfigError, load_config, parse_config
from .models import Alert, Slot, Watch
from .monitor import Monitor, SeenStore
from .notifiers import ConsoleNotifier, FileNotifier, Notifier, NotifierError, WebhookNotifier
from .providers import Provider, ProviderError, get_provider, register_provider

__version__ = "0.1.0"

__all__ = [
    "Alert",
    "Config",
    "ConfigError",
    "ConsoleNotifier",
    "FileNotifier",
    "Monitor",
    "Notifier",
    "NotifierError",
    "Provider",
    "ProviderError",
    "SeenStore",
    "Slot",
    "Watch",
    "WebhookNotifier",
    "get_provider",
    "load_config",
    "parse_config",
    "register_provider",
]
