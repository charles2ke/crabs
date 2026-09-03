# Scheduling

One-shot execution is recommended:

```bash
openclaw --config /etc/openclaw/dublin.json \
  --state /var/lib/openclaw/state.json --once --bootstrap
```

Writes use temp-file `fsync` plus atomic replacement. A neighboring `.lock` file
prevents overlap, and past appointment entries are pruned. `--bootstrap` only
suppresses the first cold run.

## GitHub Actions

`.github/workflows/watch.yml` runs every 30 minutes and manually. It restores and
saves `.openclaw` with Actions cache, serializes runs through concurrency, and
bootstraps a cold cache. Add referenced environment values as repository secrets.
Caches are branch-scoped and may be evicted; public repositories expose logs.
Provider exit `3` and lock exit `4` are warnings, while configuration failures
fail the workflow.

The cache uses a unique `openclaw-state-<run_id>` key and a shared restore
prefix, so each run restores the newest state and saves an updated copy. Actions
caches can be evicted after inactivity or under repository pressure. A cold
restore therefore enables bootstrap; slots present during that cold run are
recorded and can only produce a later change alert. The workflow concurrency
group queues instead of cancelling runs, and `--lock-timeout 30` provides a
second line of defense for shared self-hosted state.

## cron

```cron
*/20 * * * * /opt/openclaw/.venv/bin/openclaw --config /etc/openclaw/dublin.json --state /var/lib/openclaw/state.json --once --bootstrap >> /var/log/openclaw.log 2>&1
```

Cron has a minimal environment. Load secrets from a root-only (`chmod 600`) file
or environment, use absolute paths, and keep the interval polite.

## systemd

`/etc/systemd/system/openclaw.service`:

```ini
[Unit]
Description=Open Claw Schengen slot watcher (single poll)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
DynamicUser=yes
StateDirectory=openclaw
EnvironmentFile=/etc/openclaw/secrets.env
ExecStart=/opt/openclaw/.venv/bin/openclaw \
  --config /etc/openclaw/dublin.json \
  --state /var/lib/openclaw/state.json --once --bootstrap
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_INET AF_INET6
SystemCallFilter=@system-service
```

`/etc/systemd/system/openclaw.timer`:

```ini
[Unit]
Description=Run Open Claw every 20 minutes

[Timer]
OnCalendar=*:0/20
RandomizedDelaySec=300
Persistent=true
Unit=openclaw.service

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now openclaw.timer
systemctl list-timers openclaw.timer
journalctl -u openclaw.service -f
```

Protect the environment file with mode `600`. `DynamicUser` works with systemd's
managed `/var/lib/openclaw` `StateDirectory`. Random delay avoids synchronized
portal traffic; persistence catches a missed run after reboot. Twenty to thirty
minutes is generally sufficient. Honor portal limits and stop on errors or a
request to stop.
