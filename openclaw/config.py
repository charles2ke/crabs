"""Configuration loading for the Open Claw slot watcher."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    quiet_hours: Mapping[str, Any] | None = None
    throttle: Mapping[str, Any] = field(default_factory=dict)
    health: Mapping[str, Any] = field(default_factory=dict)


def missing_env_vars(value: Any) -> tuple[str, ...]:
    """Return unresolved environment-variable names without exposing values."""
    names: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            names.update(
                match.group(1)
                for match in ENV_PATTERN.finditer(item)
                if match.group(1) not in os.environ
            )
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, Mapping):
            for child in item.values():
                visit(child)

    visit(value)
    return tuple(sorted(names))


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
            if any(not isinstance(code, int) for code in success_status):
                raise ConfigError("form auth 'success_status' values must be integers")
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


def _validate_notifier_secrets(notifiers: Any) -> None:
    if notifiers is None:
        return
    if not isinstance(notifiers, list):
        return
    for spec in notifiers:
        if not isinstance(spec, Mapping):
            continue
        if str(spec.get("type", "console")).lower() != "telegram":
            continue
        if not spec.get("bot_token"):
            raise ConfigError("telegram notifier requires 'bot_token'")
        if not spec.get("chat_id"):
            raise ConfigError("telegram notifier requires 'chat_id'")
        _require_env_secret(spec.get("bot_token"), "telegram bot_token")


def _parse_clock(raw: Any, label: str) -> None:
    try:
        datetime.strptime(str(raw), "%H:%M")
    except ValueError as exc:
        raise ConfigError(f"{label} must use HH:MM") from exc


def _validate_quiet_hours(value: Any, label: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be an object")
    missing = [key for key in ("start", "end", "timezone") if not value.get(key)]
    if missing:
        raise ConfigError(f"{label} is missing: {', '.join(missing)}")
    _parse_clock(value["start"], f"{label}.start")
    _parse_clock(value["end"], f"{label}.end")
    try:
        ZoneInfo(str(value["timezone"]))
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"{label}.timezone is unknown: {value['timezone']!r}") from exc
    return dict(value)


def _validate_limits(value: Any, label: str, allowed: Iterable[str]) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ConfigError(f"{label} has unknown settings: {', '.join(sorted(unknown))}")
    result: dict[str, Any] = {}
    for key, raw in value.items():
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{label}.{key} must be numeric") from exc
        if number <= 0:
            raise ConfigError(f"{label}.{key} must be greater than 0")
        result[key] = int(number) if number.is_integer() else number
    if ("max_alerts" in result) != ("interval_seconds" in result):
        raise ConfigError(f"{label} requires both max_alerts and interval_seconds")
    return result


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
            # Auth secret validation must inspect raw placeholders before they
            # are expanded to their environment values.
            _validate_auth(entry.get("options", {}).get("auth"))
    _validate_notifier_secrets(raw_data.get("notifiers"))

    expanded = _expand_env(raw_data)
    if not isinstance(expanded, Mapping):  # pragma: no cover - shape preserved
        raise ConfigError("configuration must be a JSON object")
    data = expanded
    raw_watches = data.get("watches")
    if not isinstance(raw_watches, list):  # pragma: no cover - validated above
        raise ConfigError("configuration needs a non-empty 'watches' list")

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
        raw_alert_on = entry.get("alert_on", ["new"])
        if not isinstance(raw_alert_on, list) or not raw_alert_on:
            raise ConfigError("watch 'alert_on' must be a non-empty list")
        alert_on = tuple(str(event).lower() for event in raw_alert_on)
        invalid_events = set(alert_on) - {"new", "disappeared", "improved"}
        if invalid_events:
            raise ConfigError(
                "watch 'alert_on' has unknown events: " + ", ".join(sorted(invalid_events))
            )
        watches.append(
            Watch(
                country_from=str(entry["country_from"]),
                country_to=str(entry["country_to"]),
                city=str(entry["city"]),
                visa_category=str(entry.get("visa_category", "short-stay")),
                provider=str(entry.get("provider", "mock")),
                options=dict(options),
                alert_on=alert_on,
                quiet_hours=_validate_quiet_hours(
                    entry.get("quiet_hours"), "watch 'quiet_hours'"
                ),
                throttle=_validate_limits(
                    entry.get("throttle"),
                    "watch 'throttle'",
                    ("max_alerts", "interval_seconds", "minimum_gap_seconds"),
                ),
                health=_validate_limits(
                    entry.get("health"),
                    "watch 'health'",
                    (
                        "max_consecutive_empty",
                        "max_consecutive_errors",
                        "max_stale_hours",
                    ),
                ),
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
        quiet_hours=_validate_quiet_hours(data.get("quiet_hours"), "'quiet_hours'"),
        throttle=_validate_limits(
            data.get("throttle"),
            "'throttle'",
            ("max_alerts", "interval_seconds", "minimum_gap_seconds"),
        ),
        health=_validate_limits(
            data.get("health"),
            "'health'",
            ("max_consecutive_empty", "max_consecutive_errors", "max_stale_hours"),
        ),
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


def inspect_config(path: str | Path) -> tuple[Config, tuple[str, ...]]:
    """Load config for offline diagnostics and report missing env names."""
    config_path = Path(path).expanduser()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config {config_path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError("configuration must be a JSON object")
    missing = missing_env_vars(raw)
    if missing:
        raise ConfigError(f"missing environment variables: {', '.join(missing)}")
    config = parse_config(raw)
    return config, missing
