# State rules

How the collector turns what it observes into the public states in `status.json`, and how the
example card treats old data.

| Situation | Community server | Votebot | Discord | Game / RCON |
|---|---|---|---|---|
| Healthy, fresh, same instance | Responding | Running | Connected | Healthy |
| Gateway dropped / resuming | (unchanged) | Running | Reconnecting | (unchanged) |
| Client closed | (unchanged) | Running | Disconnected | (unchanged) |
| 1–2 failed polls | Unreachable (timeout/no connection) or Unknown (auth/API) | Running | (unchanged) | Degraded · category, **at once** |
| ≥3 failed polls | as above | Running | (unchanged) | Failing · category |
| Running, heartbeat or poll loop >180 s old | Unknown | Running + "application health unconfirmed" | Unknown | Stale / unknown |
| Snapshot missing / malformed / wrong schema | Unknown | Running + unconfirmed | Unknown | Stale / unknown |
| Restarted, snapshot from previous PID/exec | Unknown | Running + unconfirmed | Unknown | Stale / unknown |
| Stopped / failed / auto-restart | Unknown (last success = history) | Stopped (failed / restarting) | Unknown | Stale / unknown |
| Activating | Unknown | Starting | Unknown | Unknown |
| systemd query failed | Unknown | Unknown | Unknown | Unknown |
| JSON older than 120 s (collector stopped) | all Unknown in the browser, "Monitoring stale" |||
| Fetch failed, invalid JSON or no file | all Unknown, "Monitoring unavailable" |||

- **Confirmed offline is never emitted.** No authoritative signal exists. "Unreachable" means only that our current check failed.
- Being "enabled" is never consulted.
- Thresholds are configurable via `WDS_*` variables in the unit (180/180/120 s, failing after 3).
- The browser uses the web server's `Date` header as its clock. It drops green indicators if `generated_at` or any observation is past expiry, so a fresh `generated_at` never makes an old observation look fresh.
