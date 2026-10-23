# WARDOGS status monitor

Publishes a small public `status.json` that a community website can show as a live status card:
whether the game server answers, whether the [WARDOGS map vote bot](https://github.com/iServeData/wardogs_votebot)
is running, its Discord connection, its RCON link, and the current player count.

It is a read-only observer. It never restarts anything, never contacts Discord or the game server,
holds no passwords or tokens, and has no network access. Missing or old data is published as
**unknown** or **stale**, never as an outage it can't prove.

```
wardogs-votebot.service                             wardogs-status.timer (every 30 s)
  └ writes a private health snapshot every 20 s       └ wardogs-status.service (oneshot, user wdstatus)
        │                                                1 systemctl show wardogs-votebot (read-only)
        ▼                                                2 read and validate the snapshot
  /run/wardogs-health/health.json  ──read──▶             3 derive public states
  (votebot:wdhealth 0640, tmpfs)                         4 atomic write
                                                              │
                                                              ▼
                                   <your site>/wardogs/data/status.json (0644, no secrets)
                                                              │  served as a static file
                                                              ▼
                                   status card on the website (fetches every 60 s)
```

## What's in the public file

Four separate signals, each with its own observation time ([schema](docs/status.schema.json),
[state rules](docs/state_rules.md)):

| Row | Comes from | States |
|---|---|---|
| Community server | the bot's RCON status poll | responding, unreachable, unknown |
| Players | the same poll (numbers only) | current/max, or none when old |
| Votebot | systemd | running, stopped, unknown |
| Discord | the bot's gateway events | connected, reconnecting, disconnected, unknown |
| Game / RCON | the bot's RCON results | healthy, degraded, failing, unknown |

Server names are off by default. With `WDS_PUBLISH_SERVER_NAMES=1` (a systemd drop-in, for example
`/etc/systemd/system/wardogs-status.service.d/server_names.conf` with `[Service]` and
`Environment=WDS_PUBLISH_SERVER_NAMES=1`), each `community_server` gets `name`: the name the game server last
reported for itself, the same text players see in the in-game server browser. It's cleaned (no control or
invisible characters, at most 100 characters), it stays while the bot is down, and it's `null` until a vote bot
0.2.5 or newer has reported it. The bot's own labels for its servers are never published.

With several game servers (vote bot 0.2+), a `servers` list repeats the community server, players and
RCON rows for each one, identified only by the bot's server id (`main`, `server_2`, …). Your page supplies
the names. The first entry is the same as the top-level rows, so single-server cards keep working.

No player names, server names, SteamIDs, Discord IDs, IP addresses, file paths or error text are published.

## Requirements

- Linux with systemd 247 or newer, Python 3.9 or newer (standard library only; the tests need 3.11+).
- The WARDOGS map vote bot installed as `wardogs-votebot.service` (0.1.9 or newer).
- A web server that serves a folder of your site as static files.

## Install

```
git clone https://github.com/iServeData/wardogs_status_monitor.git
cd wardogs_status_monitor
sudo bash deploy/install.sh /var/www/example/wardogs/data
```

The folder is where your website serves the file from; the card then loads `/wardogs/data/status.json`.
The installer:

- creates the system user `wdstatus` (no shell, no home) and the group `wdhealth`,
- creates the private snapshot folder `/run/wardogs-health` (`/etc/tmpfiles.d/wardogs-health.conf`),
- installs the collector to `/usr/local/lib/wardogs-status/` and the service and timer,
- writes the folder setting to `/etc/systemd/system/wardogs-status.service.d/output.conf`,
- runs the collector once, shows the result and starts the 30-second timer.

To update later: `git pull`, then `sudo bash deploy/install.sh` (the folder is remembered).

### Connect the vote bot

The bot writes its health snapshot only when its `health.conf` drop-in is installed. From the vote bot's checkout:

```
sudo install -D -m 644 deploy/health.conf /etc/systemd/system/wardogs-votebot.service.d/health.conf
sudo systemctl daemon-reload
sudo systemctl restart wardogs-votebot
```

The drop-in adds one writable path (`/run/wardogs-health`) to the bot's sandbox; nothing else changes.
The bot user is not added to any group, and `wdstatus` cannot read the bot's settings.

### The website

- Copy the markup from [`examples/status_card.html`](examples/status_card.html), the `.status-card` styles
  and [`examples/status_card.js`](examples/status_card.js). Set `data-status-url` to your file's URL.
  Open the example in a browser to see the card with the bundled (deliberately old) sample data.
- The card renders with `textContent` only and uses the web server's `Date` header as its clock, so a
  visitor's wrong clock can't make old data look current.
- If you deploy the site with `rsync --delete`, exclude the data folder (`--exclude /wardogs/data/`) and
  don't change its owner, or each deploy deletes the file until the next run.
- No web server change is needed. Optional Nginx hardening, so the collector's short-lived temp files
  can't be fetched:

```
location /wardogs/data/ { add_header Cache-Control "no-store" always; location ~ /\. { deny all; } }
```

## Settings

Defaults are in `systemd/wardogs-status.service`. Override them in a drop-in
(`sudo systemctl edit wardogs-status`), not in the unit file, so updates keep them.

| Variable | Default | Meaning |
|---|---|---|
| `WDS_UNIT` | `wardogs-votebot.service` | The bot's systemd unit |
| `WDS_SNAPSHOT` | `/run/wardogs-health/health.json` | The bot's private health snapshot |
| `WDS_OUTPUT` | set by the installer | Public status file |
| `WDS_INTERVAL_SECONDS` | `30` | Published so the card knows the cadence |
| `WDS_HEARTBEAT_EXPIRY_SECONDS` | `180` | Snapshot older than this: bot health unconfirmed |
| `WDS_OBSERVATION_EXPIRY_SECONDS` | `180` | RCON or Discord observation older than this: unknown |
| `WDS_PUBLIC_EXPIRY_SECONDS` | `120` | The card treats the whole file as stale after this |
| `WDS_RCON_FAILING_AFTER` | `3` | Consecutive failed polls before "failing" (1–2 is "degraded") |
| `WDS_PUBLISH_SERVER_NAMES` | `0` | `1`: publish each game server's own name as `community_server.name` (needs vote bot 0.2.5+) |

## Day to day

```
systemctl list-timers wardogs-status.timer --no-pager       # when it runs next
journalctl -u wardogs-status.service -n 5 --no-pager -o cat  # last runs
python3 -m json.tool /var/www/example/wardogs/data/status.json
sudo systemctl disable --now wardogs-status.timer            # pause
sudo bash deploy/uninstall.sh                                # remove
```

`systemctl status wardogs-status.service` shows `inactive (dead)` between runs; that's normal for a timer
job. After uninstalling, the card shows "Monitoring unavailable". To also remove the bot's health
folder: delete the bot's `health.conf` drop-in, restart the bot, then
`sudo rm /etc/tmpfiles.d/wardogs-health.conf && sudo rm -rf /run/wardogs-health && sudo groupdel wdhealth`.

## Security model

| Account | Can write | Can read |
|---|---|---|
| `votebot` | its own paths + `/run/wardogs-health` | unchanged |
| `wdstatus` | the public data folder only | the snapshot (group `wdhealth`), systemd unit properties |
| web server | nothing new | `status.json` |

The collector runs with `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX` (only the local systemd
socket), an empty capability set, a read-only system and memory/CPU limits. See [SECURITY.md](SECURITY.md).

## Development

```
python3 -m pip install --require-hashes --no-deps -r requirements-dev.lock
python3 -m pytest -q
python3 tools/check_generic.py
```

The tests use fixtures only; they never touch a real bot, systemd, Discord or game server.

## License

[MIT](LICENSE)
