# Changelog

## 1.2.0 (2026-10-23)

- Optional server names: with `WDS_PUBLISH_SERVER_NAMES=1`, `community_server.name` (top level and per server)
  is the name the game server reports for itself, from vote bot 0.2.5+. Off by default; cleaned and at most
  100 characters; kept while the bot is down. The bot's own labels are still never published.

## 1.1.0 (2026-10-18)

- Several game servers: when the vote bot reports more than one, `status.json` gets a `servers` list with
  the community server, players and RCON state for each, by server id only. Top-level fields are unchanged.
- The example card shows one section per server when its markup lists them (`data-server="server_2"`).

## 1.0.0 (2026-10-18)

- First public version of the collector that has run in production since October 2026.
- Installer takes the website's data folder as a setting (`deploy/install.sh <folder>`) and remembers it.
- Standalone example status card (`examples/`), state rules and JSON schema in `docs/`.
- The vote bot integration is no longer a patch: vote bot 0.1.9+ writes the health snapshot itself.
