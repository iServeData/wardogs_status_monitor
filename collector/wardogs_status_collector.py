#!/usr/bin/env python3
"""WARDOGS community status collector (public schema 1).

Part of https://github.com/iServeData/wardogs_status_monitor

Runs as a systemd oneshot (wardogs-status.service) every ~30 s as the unprivileged
user `wdstatus`. It:
  1. asks systemd (read-only, `systemctl show`) for the votebot unit's structured state,
  2. reads and validates the bot's private health snapshot,
  3. derives public states with the rules in docs/state_rules.md,
  4. atomically publishes sanitised JSON to the website's status-data directory.

It never restarts anything, never contacts Discord or the game server, needs no
credentials, and publishes even when the snapshot is missing, malformed or stale.
Configuration comes from environment variables (see the unit file).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA = 1
MAX_SNAPSHOT_BYTES = 16384

RCON_CATEGORIES = {"timeout", "unreachable", "auth", "rate_limited", "api_error",
                   "invalid_response", "internal_error"}
SERVER_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_SERVERS = 10
DISCORD_STATES = {"starting", "connecting", "connected", "reconnecting", "closed"}


def env(name, default):
    return os.environ.get(name, default)


def env_int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


class Cfg:
    def __init__(self):
        self.unit = env("WDS_UNIT", "wardogs-votebot.service")
        self.snapshot = env("WDS_SNAPSHOT", "/run/wardogs-health/health.json")
        self.output = env("WDS_OUTPUT", "/var/www/html/wardogs/data/status.json")
        self.lock = env("WDS_LOCK", "/run/wardogs-status/collector.lock")
        self.interval = env_int("WDS_INTERVAL_SECONDS", 30, 10, 300)
        self.heartbeat_expiry = env_int("WDS_HEARTBEAT_EXPIRY_SECONDS", 180, 30, 3600)
        self.observation_expiry = env_int("WDS_OBSERVATION_EXPIRY_SECONDS", 180, 30, 3600)
        self.public_expiry = env_int("WDS_PUBLIC_EXPIRY_SECONDS", 120, 30, 3600)
        self.failing_after = env_int("WDS_RCON_FAILING_AFTER", 3, 1, 50)
        self.systemctl_timeout = env_int("WDS_SYSTEMCTL_TIMEOUT_SECONDS", 5, 1, 15)
        self.systemctl = env("WDS_SYSTEMCTL", "systemctl")


def iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


# --------------------------------------------------------------------------- systemd
PROPS = ["LoadState", "ActiveState", "SubState", "MainPID", "Result", "ExecMainStartTimestamp",
         "ExecMainStartTimestampMonotonic", "ActiveEnterTimestampMonotonic"]


def query_unit(cfg) -> dict:
    """Returns {'ok': bool, ...props}. Being 'enabled' is never consulted."""
    try:
        r = subprocess.run([cfg.systemctl, "show", cfg.unit, "--no-pager", "--property=" + ",".join(PROPS)],
                           capture_output=True, text=True, timeout=cfg.systemctl_timeout,
                           env={"PATH": "/usr/bin:/bin", "LANG": "C", "SYSTEMD_PAGER": ""})
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False}
    if r.returncode != 0:
        return {"ok": False}
    props = {}
    for line in r.stdout.splitlines():
        k, _, v = line.partition("=")
        props[k.strip()] = v.strip()
    props["ok"] = "ActiveState" in props and "LoadState" in props
    try:  # wall-clock time the current main process was exec'd (CLOCK_MONOTONIC is system-wide)
        mono = int(props.get("ExecMainStartTimestampMonotonic") or 0)
        if mono > 0:
            props["exec_start_wall"] = time.time() - (time.monotonic() - mono / 1e6)
    except ValueError:
        pass
    return props


def same_instance(unit: dict, snap: dict | None) -> bool:
    """Snapshot was written by the process systemd currently reports as MainPID."""
    if snap is None or str(snap.get("pid")) != str(unit.get("MainPID")) or unit.get("MainPID") in (None, "0"):
        return False
    start = unit.get("exec_start_wall")
    return start is None or num(snap.get("process_started_at")) >= start - 10


def process_state(u: dict) -> tuple[str, str | None]:
    """-> (state, detail). state in running|stopped|unknown; detail starting|failed|stopping|restarting|None."""
    if not u.get("ok"):
        return "unknown", "query_failed"
    if u.get("LoadState") != "loaded":
        return "unknown", "unit_not_found"
    a, s = u.get("ActiveState"), u.get("SubState")
    if a == "active" and s == "running":
        return "running", None
    if a == "activating":
        return ("stopped", "restarting") if s == "auto-restart" else ("unknown", "starting")
    if a == "deactivating":
        return "unknown", "stopping"
    if a == "failed":
        return "stopped", "failed"
    if a == "inactive":
        return "stopped", None
    if a == "active":           # e.g. active/exited: not a running bot
        return "unknown", s or None
    return "unknown", None


# --------------------------------------------------------------------------- snapshot
def read_snapshot(path) -> tuple[dict | None, str]:
    """-> (snapshot or None, reason). reason: ok|missing|unreadable|malformed|schema."""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_SNAPSHOT_BYTES + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > MAX_SNAPSHOT_BYTES:
        return None, "malformed"
    try:
        s = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "malformed"
    if not isinstance(s, dict) or s.get("schema_version") != SNAPSHOT_SCHEMA:
        return None, "schema"
    d, r = s.get("discord"), s.get("rcon")
    if (not isinstance(s.get("instance_id"), str) or not isinstance(s.get("pid"), int)
            or num(s.get("written_at")) is None or num(s.get("process_started_at")) is None
            or not isinstance(d, dict) or not isinstance(r, dict)
            or d.get("state") not in DISCORD_STATES):
        return None, "malformed"
    lr = r.get("last_result")
    if lr is not None and lr != "ok" and lr not in RCON_CATEGORIES:
        return None, "malformed"
    # Several game servers (vote bot 0.2+): keep well-formed entries, drop anything else.
    servers, raw = [], s.get("servers")
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not SERVER_ID.match(x["id"]):
            continue
        xr = x.get("rcon")
        if not isinstance(xr, dict):
            continue
        xl = xr.get("last_result")
        if xl is not None and xl != "ok" and xl not in RCON_CATEGORIES:
            continue
        if x["id"] not in {y["id"] for y in servers} and len(servers) < MAX_SERVERS:
            servers.append({"id": x["id"], "rcon": xr, "players": x.get("players")})
    s["servers"] = servers
    return s, "ok"


def players_from(p, now: float, expiry: int):
    """{'current': int, 'max': int|None, 'observed_at': iso} or None. Numbers only; anything
    missing, implausible or older than the observation expiry is dropped."""
    if not isinstance(p, dict):
        return None
    cur, mx, at = p.get("current"), p.get("max"), num(p.get("observed_at"))
    def ok_int(v):
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1000
    if not ok_int(cur) or at is None or now - at > expiry or at > now + 30:
        return None
    if not ok_int(mx) or mx == 0 or cur > mx:
        mx = None
    return {"current": cur, "max": mx, "observed_at": iso(at)}


def server_states(cfg, rs: dict | None, players, app: str, app_reason, now: float) -> tuple[dict, dict]:
    """(community_server, rcon) for one game server from its RCON poll results."""
    r = {"state": "unknown", "category": None, "source": "rcon_status_poll", "last_attempt_at": None,
         "last_success_at": None, "consecutive_failures": None, "detail": None}
    srv = {"state": "unknown", "source": "rcon", "source_label": "Via RCON", "observed_at": None,
           "last_success_at": None, "shares_evidence_with": "rcon", "detail": None, "players": None}
    if rs is not None:
        # historical only unless the evidence is current and from the running instance
        last_ok = num(rs.get("last_success_at"))
        r["last_success_at"] = srv["last_success_at"] = iso(last_ok)
    if app == "confirmed" and rs is not None:
        attempt = num(rs.get("last_attempt_at"))
        result = rs.get("last_result")
        fails = rs.get("consecutive_failures") if isinstance(rs.get("consecutive_failures"), int) else None
        r["last_attempt_at"] = iso(attempt)
        r["consecutive_failures"] = fails
        if attempt is None or result is None:
            r["detail"] = srv["detail"] = "no_attempt_yet"
        elif now - attempt > cfg.observation_expiry:
            r["detail"] = srv["detail"] = "observation_stale"
        elif result == "ok":
            r["state"] = "healthy"
            srv["state"], srv["observed_at"] = "responding", iso(num(rs.get("last_success_at")) or attempt)
            srv["players"] = players_from(players, now, cfg.observation_expiry)
        else:
            # A failure that was observed is shown at once - no waiting for expiry.
            r["category"] = result
            r["state"] = "failing" if (fails or 1) >= cfg.failing_after else "degraded"
            srv["observed_at"] = iso(attempt)
            if result in ("timeout", "unreachable"):
                srv["state"] = "unreachable"   # current failed check; NOT proof the server is down
            else:
                srv["detail"] = "rcon_" + result   # API answered but check not validated
    else:
        r["detail"] = srv["detail"] = app_reason
    return srv, r


# --------------------------------------------------------------------------- derive
def derive(cfg, unit: dict, snap: dict | None, snap_reason: str, now: float) -> dict:
    proc, proc_detail = process_state(unit)
    out = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "demo": False,
        "expiry": {
            "public_json_seconds": cfg.public_expiry,
            "heartbeat_seconds": cfg.heartbeat_expiry,
            "observation_seconds": cfg.observation_expiry,
        },
        "collector": {"interval_seconds": cfg.interval},
    }
    # ---- application evidence validity
    app = "unknown"
    app_reason = None
    if proc != "running":
        app_reason = "bot_not_running" if proc == "stopped" else (proc_detail or "process_unknown")
    elif snap is None:
        app, app_reason = "unconfirmed", "snapshot_" + snap_reason
    else:
        written = num(snap.get("written_at"))
        progress = num(snap.get("loop_progress_at"))
        if not same_instance(unit, snap):
            app, app_reason = "unconfirmed", "previous_instance"
        elif written > now + 30:
            app, app_reason = "unconfirmed", "clock_skew"
        elif now - written > cfg.heartbeat_expiry:
            app, app_reason = "unconfirmed", "heartbeat_stale"
        elif progress is None and now - num(snap["process_started_at"]) > cfg.heartbeat_expiry:
            app, app_reason = "unconfirmed", "poll_loop_not_started"
        elif progress is not None and now - progress > cfg.heartbeat_expiry:
            app, app_reason = "unconfirmed", "poll_loop_stalled"
        else:
            app = "confirmed"
    current = proc == "running" and same_instance(unit, snap)

    # ---- votebot process
    out["votebot"] = {"state": proc, "detail": proc_detail, "source": "systemd", "checked_at": iso(now),
                      "application_health": app, "application_reason": app_reason}

    # ---- discord
    d = {"state": "unknown", "detail": None, "source": "discord_gateway_events",
         "observed_at": None, "last_connected_at": None}
    if current:
        d["last_connected_at"] = iso(num(snap["discord"].get("last_ready_at")))
    if app == "confirmed":
        ds = snap["discord"]["state"]
        d["observed_at"] = iso(num(snap["discord"].get("observed_at")) or num(snap["written_at"]))
        d["state"] = {"connected": "connected", "reconnecting": "reconnecting", "connecting": "reconnecting",
                      "starting": "reconnecting", "closed": "disconnected"}[ds]
        if ds in ("connecting", "starting"):
            d["detail"] = ds
    else:
        d["detail"] = app_reason
    out["discord"] = d

    # ---- rcon (+ community server, which shares the same evidence), first server at the top level
    srv, r = server_states(cfg, snap.get("rcon") if snap else None, snap.get("players") if snap else None,
                           app, app_reason, now)
    out["rcon"] = r
    out["community_server"] = srv
    # ---- every server, when the bot serves more than one (the first one repeats the top level)
    if snap is not None and snap.get("servers"):
        out["servers"] = []
        for x in snap["servers"]:
            xs, xr = server_states(cfg, x["rcon"], x.get("players"), app, app_reason, now)
            out["servers"].append({"id": x["id"], "community_server": xs, "rcon": xr})
    # Confirmed offline would require an authoritative signal; none exists, so never emitted.

    # ---- overall monitoring state
    if app == "confirmed":
        mon = "ok"
    elif proc in ("running", "stopped"):
        mon = "degraded"
    else:
        mon = "unknown"
    out["monitoring"] = {"state": mon, "snapshot": snap_reason}
    return out


# --------------------------------------------------------------------------- publish
def publish(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    body = (json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n").encode()
    fd, tmp = tempfile.mkstemp(prefix=".status.", suffix=".tmp", dir=d)
    try:
        try:
            os.write(fd, body)
            os.fchmod(fd, 0o644)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    cfg = Cfg()
    lockf = None
    try:
        os.makedirs(os.path.dirname(cfg.lock), exist_ok=True)
        lockf = open(cfg.lock, "w")
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another collector run is active; skipping", file=sys.stderr)
        return 0
    except OSError:
        lockf = None  # no lock dir (manual run): systemd already prevents overlap
    unit = query_unit(cfg)
    snap, reason = read_snapshot(cfg.snapshot)
    data = derive(cfg, unit, snap, reason, time.time())
    publish(cfg.output, data)
    pl = data["community_server"].get("players")
    print("published: monitoring=%s votebot=%s discord=%s rcon=%s server=%s players=%s" % (
        data["monitoring"]["state"], data["votebot"]["state"], data["discord"]["state"],
        data["rcon"]["state"], data["community_server"]["state"],
        ("%s/%s" % (pl["current"], pl["max"] if pl["max"] is not None else "?")) if pl else "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
