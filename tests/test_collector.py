"""Scenario tests for the collector. Uses fixtures/mocks only: never touches a real
bot, systemd unit, Discord or game server.   Run:  python3 -m pytest -q tests"""
import json, os, pathlib, stat, sys, time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "collector"))
import wardogs_status_collector as c  # noqa: E402

NOW = 1_791_500_000.0          # fixed clock (2026-10-08 UTC)
PID = 4242
SECRET_STRINGS = ["76561198", "SecretPlayer", "hunter2", "203.0.113.7", "198.51.100.9",
                  "/run/wardogs-health", "/var/lib", "Traceback", "discord_token", "1234567890123456"]


def cfg(**kw):
    x = c.Cfg()
    for k, v in kw.items():
        setattr(x, k, v)
    return x


def unit(active="active", sub="running", pid=PID, start=NOW - 3600, load="loaded"):
    return {"ok": True, "LoadState": load, "ActiveState": active, "SubState": sub,
            "MainPID": str(pid if active == "active" else 0), "exec_start_wall": start}


def snap(age=10, pid=PID, started=NOW - 3590, discord="connected", rcon_result="ok",
         rcon_age=12, ok_age=12, fails=0, progress_age=None):
    w = NOW - age
    return {
        "schema_version": 1, "instance_id": "a1b2c3d4e5f6a7b8", "pid": pid,
        "process_started_at": started, "written_at": w,
        "loop_progress_at": NOW - (progress_age if progress_age is not None else rcon_age),
        "health_interval_s": 20,
        "discord": {"state": discord, "since": NOW - 3000, "last_ready_at": NOW - 3000, "observed_at": w},
        "rcon": {"last_attempt_at": NOW - rcon_age, "last_result": rcon_result,
                 "last_success_at": (NOW - ok_age) if ok_age is not None else None,
                 "consecutive_failures": fails},
    }


def d(u, s, reason="ok", **kw):
    return c.derive(cfg(**kw), u, s, reason if s is not None else reason, NOW)


# ---------------------------------------------------------------- scenarios
def test_healthy():
    o = d(unit(), snap())
    assert o["monitoring"]["state"] == "ok"
    assert o["votebot"]["state"] == "running" and o["votebot"]["application_health"] == "confirmed"
    assert o["discord"]["state"] == "connected"
    assert o["rcon"]["state"] == "healthy"
    assert o["community_server"]["state"] == "responding"
    assert o["community_server"]["source_label"] == "Via RCON"
    assert o["generated_at"].endswith("Z")


def test_running_discord_disconnected():
    for raw, want in (("reconnecting", "reconnecting"), ("closed", "disconnected"), ("connecting", "reconnecting")):
        o = d(unit(), snap(discord=raw))
        assert o["discord"]["state"] == want
        assert o["rcon"]["state"] == "healthy"   # independent component unaffected


def test_running_rcon_failing_shown_immediately():
    # first failure: degraded right away even though last success is only 27 s old
    o = d(unit(), snap(rcon_result="timeout", rcon_age=5, ok_age=27, fails=1))
    assert o["rcon"]["state"] == "degraded" and o["rcon"]["category"] == "timeout"
    assert o["community_server"]["state"] == "unreachable"
    assert o["rcon"]["last_success_at"] is not None          # history kept
    o = d(unit(), snap(rcon_result="unreachable", rcon_age=5, ok_age=120, fails=6))
    assert o["rcon"]["state"] == "failing"
    o = d(unit(), snap(rcon_result="auth", rcon_age=5, ok_age=400, fails=4))
    assert o["rcon"]["state"] == "failing" and o["rcon"]["category"] == "auth"
    assert o["community_server"]["state"] == "unknown"       # API answered; not "unreachable"
    assert o["community_server"]["state"] != "offline"


def test_running_stale_heartbeat():
    o = d(unit(), snap(age=400, rcon_age=400))
    assert o["votebot"]["state"] == "running"
    assert o["votebot"]["application_health"] == "unconfirmed"
    assert o["votebot"]["application_reason"] == "heartbeat_stale"
    assert o["discord"]["state"] == "unknown" and o["rcon"]["state"] == "unknown"
    assert o["community_server"]["state"] == "unknown"


def test_running_poll_loop_stalled_but_event_loop_alive():
    o = d(unit(), snap(age=10, rcon_age=600, progress_age=600))
    assert o["votebot"]["application_reason"] == "poll_loop_stalled"
    assert o["rcon"]["state"] == "unknown"


def test_stale_rcon_observation():
    o = d(unit(), snap(rcon_age=300, progress_age=10))
    assert o["rcon"]["state"] == "unknown" and o["rcon"]["detail"] == "observation_stale"


def test_stopped_with_old_snapshot():
    for active, sub, detail in (("inactive", "dead", None), ("failed", "failed", "failed"),
                                ("activating", "auto-restart", "restarting")):
        o = d(unit(active=active, sub=sub), snap(age=5))
        assert o["votebot"]["state"] == "stopped" and o["votebot"]["detail"] == detail
        assert o["discord"]["state"] == "unknown" and o["discord"]["observed_at"] is None
        assert o["rcon"]["state"] == "unknown" and o["rcon"]["last_attempt_at"] is None
        assert o["rcon"]["last_success_at"] is not None       # historical only
        assert o["community_server"]["state"] == "unknown"


def test_starting():
    o = d(unit(active="activating", sub="start"), None, "missing")
    assert o["votebot"]["state"] == "unknown" and o["votebot"]["detail"] == "starting"


def test_restarted_with_old_instance_snapshot():
    o = d(unit(pid=5555, start=NOW - 20), snap(age=25, pid=PID))
    assert o["votebot"]["application_reason"] == "previous_instance"
    assert o["discord"]["state"] == "unknown" and o["rcon"]["state"] == "unknown"
    # PID reuse: same PID but snapshot older than the current exec
    o = d(unit(start=NOW - 20), snap(age=5, started=NOW - 5000))
    assert o["votebot"]["application_reason"] == "previous_instance"


def test_missing_and_malformed_snapshot(tmp_path):
    s, r = c.read_snapshot(str(tmp_path / "nope.json"))
    assert s is None and r == "missing"
    for body, reason in ((b"{not json", "malformed"), (b'{"schema_version":99}', "schema"),
                         (b'{"schema_version":1,"pid":"x"}', "malformed"), (b"x" * 20000, "malformed")):
        p = tmp_path / "h.json"; p.write_bytes(body)
        s, r = c.read_snapshot(str(p))
        assert s is None and r == reason
        o = d(unit(), None, r)
        assert o["votebot"]["state"] == "running"
        assert o["votebot"]["application_health"] == "unconfirmed"
        assert o["discord"]["state"] == "unknown"


def test_enabled_is_not_running():
    o = d({"ok": True, "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
           "MainPID": "0", "UnitFileState": "enabled"}, None, "missing")
    assert o["votebot"]["state"] == "stopped"


def test_systemctl_unavailable():
    o = d({"ok": False}, snap())
    assert o["votebot"]["state"] == "unknown"
    assert o["discord"]["state"] == "unknown" and o["rcon"]["state"] == "unknown"


def test_never_confirmed_offline():
    for s in (snap(rcon_result="unreachable", fails=99), None):
        o = d(unit(), s, "missing")
        assert o["community_server"]["state"] in ("unreachable", "unknown")


def test_old_observation_not_refreshed_by_new_generated_at():
    o = d(unit(), snap(rcon_result="ok", rcon_age=170, ok_age=170, progress_age=5))
    assert o["community_server"]["observed_at"] == c.iso(NOW - 170)
    assert o["generated_at"] == c.iso(NOW)


# ---------------------------------------------------------------- end-to-end main()
def fake_systemctl(tmp_path, props: str):
    p = tmp_path / "systemctl"
    p.write_text("#!/bin/sh\ncat <<'EOF'\n" + props + "\nEOF\n")
    p.chmod(0o755)
    return str(p)


def test_end_to_end_atomic_publication_and_secret_free(tmp_path, monkeypatch):
    out = tmp_path / "data"; out.mkdir()
    sc = fake_systemctl(tmp_path, "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=%d\n"
                                  "Result=success\nExecMainStartTimestampMonotonic=0" % os.getpid())
    s = snap(); s["pid"] = os.getpid(); s["written_at"] = time.time(); s["loop_progress_at"] = time.time()
    s["rcon"]["last_attempt_at"] = s["rcon"]["last_success_at"] = time.time()
    hp = tmp_path / "health.json"; hp.write_text(json.dumps(s))
    for k, v in {"WDS_SYSTEMCTL": sc, "WDS_SNAPSHOT": str(hp), "WDS_OUTPUT": str(out / "status.json"),
                 "WDS_LOCK": str(tmp_path / "lock")}.items():
        monkeypatch.setenv(k, v)
    assert c.main() == 0
    f = out / "status.json"
    raw = f.read_text()
    o = json.loads(raw)
    assert o["schema_version"] == 1 and o["rcon"]["state"] == "healthy"
    assert stat.S_IMODE(f.stat().st_mode) == 0o644
    assert sorted(os.listdir(out)) == ["status.json"]       # no temp files left behind
    for bad in SECRET_STRINGS:
        assert bad not in raw
    assert "pid" not in raw and "instance_id" not in raw

    # publication failure leaves the previous file intact (atomic replace)
    before = f.read_bytes()
    monkeypatch.setattr(c.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        c.publish(str(f), {"x": 1})
    assert f.read_bytes() == before and sorted(os.listdir(out)) == ["status.json"]


def test_main_publishes_when_snapshot_missing(tmp_path, monkeypatch):
    out = tmp_path / "data"; out.mkdir()
    sc = fake_systemctl(tmp_path, "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=1")
    for k, v in {"WDS_SYSTEMCTL": sc, "WDS_SNAPSHOT": str(tmp_path / "none.json"),
                 "WDS_OUTPUT": str(out / "status.json"), "WDS_LOCK": str(tmp_path / "lock")}.items():
        monkeypatch.setenv(k, v)
    assert c.main() == 0
    o = json.loads((out / "status.json").read_text())
    assert o["monitoring"]["snapshot"] == "missing" and o["discord"]["state"] == "unknown"


def test_overlapping_run_skips(tmp_path, monkeypatch):
    import fcntl
    lock = tmp_path / "lock"
    fh = open(lock, "w"); fcntl.flock(fh, fcntl.LOCK_EX)
    monkeypatch.setenv("WDS_LOCK", str(lock))
    monkeypatch.setenv("WDS_OUTPUT", str(tmp_path / "never.json"))
    assert c.main() == 0
    assert not (tmp_path / "never.json").exists()


# ---------------------------------------------------------------- demo fixtures for the webpage
SCENARIOS = {
    "healthy": (unit(), snap()),
    "discord-disconnected": (unit(), snap(discord="reconnecting")),
    "rcon-failing": (unit(), snap(rcon_result="timeout", rcon_age=8, ok_age=200, fails=7)),
    "stale-heartbeat": (unit(), snap(age=400, rcon_age=400)),
    "bot-stopped": (unit(active="inactive", sub="dead"), snap(age=900, rcon_age=900, ok_age=900)),
    "old-instance": (unit(pid=5555, start=NOW - 20), snap(age=25)),
    "snapshot-missing": (unit(), None),
}


def test_write_demo_fixtures():
    fx = HERE / "fixtures"; fx.mkdir(exist_ok=True)
    for name, (u, s) in SCENARIOS.items():
        o = c.derive(cfg(), u, s, "ok" if s else "missing", NOW)
        o["demo"] = True   # every fixture is explicitly labelled as demo data
        (fx / f"{name}.json").write_text(json.dumps(o, indent=2, sort_keys=True) + "\n")
    assert len(list(fx.glob("*.json"))) == len(SCENARIOS)


# ---------------------------------------------------------------- player population (v2)
def with_players(s, cur=12, mx=98, age=12):
    s["players"] = {"current": cur, "max": mx, "observed_at": NOW - age}
    return s


def test_players_published_when_responding():
    o = d(unit(), with_players(snap()))
    assert o["community_server"]["players"]["current"] == 12
    assert o["community_server"]["players"]["max"] == 98


def test_players_absent_in_old_snapshot():
    o = d(unit(), snap())                       # bot without the v2 health patch
    assert o["community_server"]["state"] == "responding"
    assert o["community_server"]["players"] is None


def test_players_dropped_when_stale_or_failing_or_bot_down():
    assert d(unit(), with_players(snap(), age=999))["community_server"]["players"] is None
    assert d(unit(), with_players(snap(rcon_result="timeout", fails=3)))["community_server"]["players"] is None
    assert d(unit(active="inactive", sub="dead"), with_players(snap()))["community_server"]["players"] is None


def test_players_implausible_values():
    assert d(unit(), with_players(snap(), cur=-1))["community_server"]["players"] is None
    assert d(unit(), with_players(snap(), cur="12"))["community_server"]["players"] is None
    assert d(unit(), with_players(snap(), cur=True))["community_server"]["players"] is None
    p = d(unit(), with_players(snap(), cur=50, mx=40))["community_server"]["players"]
    assert p["current"] == 50 and p["max"] is None
    p = d(unit(), with_players(snap(), mx=None))["community_server"]["players"]
    assert p["current"] == 12 and p["max"] is None


# ---------------------------------------------------------------- several game servers (vote bot 0.2+)
def two_servers(second_result="ok", second_age=12):
    s = snap()
    s["players"] = {"current": 7, "max": 100, "observed_at": NOW - 12}
    s["servers"] = [
        {"id": "main", "name": "Community | Server One | discord.gg/abc", "rcon": dict(s["rcon"]), "players": s["players"]},
        {"id": "server_2", "name": "Community | Server Two | SecretPlayer",
         "rcon": {"last_attempt_at": NOW - second_age, "last_result": second_result,
                  "last_success_at": NOW - 300, "consecutive_failures": 0 if second_result == "ok" else 4},
         "players": {"current": 3, "max": 64, "observed_at": NOW - second_age}},
    ]
    return s


def read_back(tmp_path, s):
    p = tmp_path / "health.json"
    p.write_text(json.dumps(s))
    return c.read_snapshot(str(p))


def test_each_server_is_published_by_id(tmp_path):
    s, reason = read_back(tmp_path, two_servers())
    o = d(unit(), s, reason)
    assert [x["id"] for x in o["servers"]] == ["main", "server_2"]
    west, mid = o["servers"]
    assert west["community_server"] == o["community_server"] and west["rcon"] == o["rcon"]   # first = top level
    assert mid["community_server"]["state"] == "responding" and mid["community_server"]["players"]["current"] == 3
    assert mid["rcon"]["state"] == "healthy"
    text = json.dumps(o)
    assert "Server Two" not in text and "discord.gg" not in text and "SecretPlayer" not in text   # names never published


def test_one_server_failing_does_not_affect_the_other(tmp_path):
    s, reason = read_back(tmp_path, two_servers(second_result="timeout"))
    o = d(unit(), s, reason)
    west, mid = o["servers"]
    assert west["rcon"]["state"] == "healthy" and o["rcon"]["state"] == "healthy"
    assert (mid["rcon"]["state"], mid["rcon"]["category"], mid["community_server"]["state"]) == ("failing", "timeout", "unreachable")
    assert mid["community_server"]["players"] is None


def test_servers_unknown_when_bot_health_unconfirmed(tmp_path):
    s, reason = read_back(tmp_path, two_servers())
    o = d(unit(active="inactive", sub="dead"), s, reason)
    assert all(x["community_server"]["state"] == "unknown" and x["rcon"]["state"] == "unknown" for x in o["servers"])


def test_bad_server_entries_are_dropped_not_fatal(tmp_path):
    s = two_servers()
    s["servers"] += [{"id": "Bad Id!", "rcon": {}}, {"id": "x3", "rcon": "nope"},
                     {"id": "x4", "rcon": {"last_result": "something_new"}}, {"id": "main", "rcon": {}}, "junk"]
    s2, reason = read_back(tmp_path, s)
    assert reason == "ok" and [x["id"] for x in s2["servers"]] == ["main", "server_2"]
    s["servers"] = "not a list"
    s3, reason = read_back(tmp_path, s)
    assert reason == "ok" and s3["servers"] == [] and "servers" not in d(unit(), s3, reason)


def test_single_server_snapshot_has_no_servers_list():
    assert "servers" not in d(unit(), snap())
