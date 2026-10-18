# Changelog

## 1.0.0 (2026-10-18)

- First public version of the collector that has run in production since October 2026.
- Installer takes the website's data folder as a setting (`deploy/install.sh <folder>`) and remembers it.
- Standalone example status card (`examples/`), state rules and JSON schema in `docs/`.
- The vote bot integration is no longer a patch: vote bot 0.1.9+ writes the health snapshot itself.
