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

## cron

```cron
*/20 * * * * /opt/openclaw/.venv/bin/openclaw --config /etc/openclaw/dublin.json --state /var/lib/openclaw/state.json --once --bootstrap >> /var/log/openclaw.log 2>&1
```

Cron has a minimal environment. Load secrets from a root-only (`chmod 600`) file
or environment, use absolute paths, and keep the interval polite.

## systemd

Use a hardened `Type=oneshot` service with `DynamicUser=yes`,
`StateDirectory=openclaw`, a protected `EnvironmentFile`, `NoNewPrivileges=yes`,
`ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, and restricted
network address families. Trigger it with:

```ini
[Timer]
OnCalendar=*:0/20
RandomizedDelaySec=300
Persistent=true
```

Enable with `systemctl enable --now openclaw.timer`. Random delay avoids synchronized
portal traffic; persistence catches a missed run after reboot. Twenty to thirty
minutes is generally sufficient. Honor portal limits and stop on errors or a
request to stop.
