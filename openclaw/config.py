"""Configuration loading for the Open Claw slot watcher."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from .models import Watch

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ENV_ONLY_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
PASSWORDISH_KEYS = {"password", "pass", "secret", "api_key", "apikey", "access_token"}


class ConfigError(ValueError):
    """Raised when a configuration file is invalid."""


@dataclass(frozen=True)
class Config:
    """A full watcher configuration."""

    watches: tuple[Watch, ...]
    notifiers: tuple[Mapping[str, Any], ...] = ()
    poll_interval: float = 300.0
    earliest: date | None = None
    latest: date | None = None
    state_file: Path | None = None
    jitter: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _expand_env(value: Any) -> Any:
    """Replace ``${VAR}`` placeholders in strings with environment values.

    This keeps API keys and webhook URLs out of committed config files.
    """
    if isinstance(value, str):
        return ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def _parse_date(raw: Any, label: str) -> date | None:
    if raw in (None, ""):
        return None
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(f"invalid {label} date {raw!r}: expected YYYY-MM-DD") from exc


def _require_env_secret(value: Any, label: str) -> None:
    if isinstance(value, str) and value and not ENV_ONLY_PATTERN.fullmatch(value):
        raise ConfigError(
            f"{label} must use a ${{ENV_VAR}} placeholder, not a literal secret"
        )


def _is_passwordish_key(key: str) -> bool:
    key = key.lower()
    return key in PASSWORDISH_KEYS or "password" in key or key.endswith("_secret")


def _validate_auth(auth: Any) -> None:
    if auth is None:
        return
    if not isinstance(auth, Mapping):
        raise ConfigError("watch 'options.auth' must be an object")
    auth_type = str(auth.get("type", "none")).lower()
    if auth_type not in {"none", "form", "token", "basic"}:
        raise ConfigError("watch 'options.auth.type' must be one of: none, form, token, basic")
    if auth_type == "none":
        return

    login_url = auth.get("login_url")
    if auth_type in {"form", "token"} and not login_url:
        raise ConfigError(f"watch 'options.auth.login_url' is required for {auth_type} auth")

    if auth_type == "basic":
        if not auth.get("username") or not auth.get("password"):
            raise ConfigError("basic auth requires 'username' and 'password'")
        _require_env_secret(auth.get("password"), "basic auth password")
        return

    if auth_type == "form":
        fields = auth.get("fields")
        if not isinstance(fields, Mapping) or not fields:
            raise ConfigError("form auth requires a non-empty 'fields' object")
        for key, value in fields.items():
            if _is_passwordish_key(str(key)):
                _require_env_secret(value, f"form auth field {key!r}")
        encoding = str(auth.get("encoding", "form")).lower()
        if encoding not in {"form", "json"}:
            raise ConfigError("form auth 'encoding' must be 'form' or 'json'")
        if "success_status" in auth:
            success_status = auth["success_status"]
            if not isinstance(success_status, list) or not success_status:
                raise ConfigError("form auth 'success_status' must be a non-empty list")
            try:
                for code in success_status:
                    int(code)
            except (TypeError, ValueError) as exc:
                raise ConfigError("form auth 'success_status' values must be integers") from exc
        csrf = auth.get("csrf")
        if csrf is not None:
            if not isinstance(csrf, Mapping):
                raise ConfigError("form auth 'csrf' must be an object")
            if not csrf.get("regex") or not csrf.get("field"):
                raise ConfigError("form auth 'csrf' requires 'regex' and 'field'")
        return

    body = auth.get("body")
    if not isinstance(body, Mapping) or not body:
        raise ConfigError("token auth requires a non-empty 'body' object")
    for key, value in body.items():
        if _is_passwordish_key(str(key)):
            _require_env_secret(value, f"token auth body field {key!r}")


def parse_config(data: Mapping[str, Any]) -> Config:
    """Build a :class:`Config` from an already-parsed mapping."""
    if not isinstance(data, Mapping):
        raise ConfigError("configuration must be a JSON object")

    raw_data = dict(data)
    raw_watches = raw_data.get("watches")
    if not isinstance(raw_watches, list) or not raw_watches:
        raise ConfigError("configuration needs a non-empty 'watches' list")
    for entry in raw_watches:
        if isinstance(entry, Mapping) and isinstance(entry.get("options", {}), Mapping):
            _validate_auth(entry.get("options", {}).get("auth"))

    data = _expand_env(raw_data)
    raw_watches = data.get("watches")

    watches: list[Watch] = []
    for entry in raw_watches:
        if not isinstance(entry, Mapping):
            raise ConfigError("each watch must be an object")
        missing = [key for key in ("country_from", "country_to", "city") if not entry.get(key)]
        if missing:
            raise ConfigError(f"watch is missing required keys: {', '.join(missing)}")
        options = entry.get("options", {})
        if not isinstance(options, Mapping):
            raise ConfigError("watch 'options' must be an object")
        watches.append(
            Watch(
                country_from=str(entry["country_from"]),
                country_to=str(entry["country_to"]),
                city=str(entry["city"]),
                visa_category=str(entry.get("visa_category", "short-stay")),
                provider=str(entry.get("provider", "mock")),
                options=dict(options),
            )
        )

    raw_notifiers = data.get("notifiers") or [{"type": "console"}]
    if not isinstance(raw_notifiers, list):
        raise ConfigError("'notifiers' must be a list")
    for spec in raw_notifiers:
        if not isinstance(spec, Mapping):
            raise ConfigError("each notifier must be an object")

    try:
        poll_interval = float(data.get("poll_interval", 300.0))
        jitter = float(data.get("jitter", 0.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid numeric setting: {exc}") from exc
    if poll_interval <= 0:
        raise ConfigError("'poll_interval' must be greater than 0 seconds")
    if jitter < 0:
        raise ConfigError("'jitter' cannot be negative")

    earliest = _parse_date(data.get("earliest"), "earliest")
    latest = _parse_date(data.get("latest"), "latest")
    if earliest and latest and earliest > latest:
        raise ConfigError("'earliest' must not be after 'latest'")

    state_file = data.get("state_file")
    return Config(
        watches=tuple(watches),
        notifiers=tuple(dict(spec) for spec in raw_notifiers),
        poll_interval=poll_interval,
        earliest=earliest,
        latest=latest,
        state_file=Path(str(state_file)).expanduser() if state_file else None,
        jitter=jitter,
        metadata=dict(data.get("metadata") or {}),
    )


def load_config(path: str | Path) -> Config:
    """Load and validate a JSON configuration file."""
    config_path = Path(path).expanduser()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config {config_path} is not valid JSON: {exc}") from exc
    return parse_config(raw)
