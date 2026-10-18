# Security

## Reporting a problem

Please don't open a public issue for a security problem. Use GitHub's **Report a vulnerability**
(Security tab → Advisories) on this repository. Include what you found and how to reproduce it.

## What it protects

- **No secrets to leak**: the collector has no Discord token, RCON password or network access.
- **The public file**: only states, timestamps and player counts. Names, SteamIDs, IDs, addresses, paths and
  error text are never copied from the snapshot (the tests check this).
- **The rest of the server**: it runs as `wdstatus`, writes only the public data folder, and can't restart
  or change anything.

## Deployment checklist

- Keep the unit's sandboxing (`systemd-analyze security wardogs-status`).
- Don't add `wdstatus` to the bot's group, and don't make `/run/wardogs-health` world-readable.
- Never commit real paths, domains, IDs or addresses. CI runs `tools/check_generic.py`.

## Supported versions

Only the latest release gets fixes.
