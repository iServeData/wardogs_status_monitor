#!/usr/bin/env bash
# Remove the status collector. The vote bot keeps running; the website card then shows
# "Monitoring unavailable". The bot's health folder (/run/wardogs-health, group wdhealth)
# is left in place because the bot may still write to it; see README to remove it too.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
DROPIN=/etc/systemd/system/wardogs-status.service.d/output.conf
OUT_DIR=$( [[ -f $DROPIN ]] && sed -n 's/^ReadWritePaths=//p' "$DROPIN" | head -1 || true)
systemctl disable --now wardogs-status.timer 2>/dev/null || true
systemctl stop wardogs-status.service 2>/dev/null || true
rm -f /etc/systemd/system/wardogs-status.service /etc/systemd/system/wardogs-status.timer
rm -rf /etc/systemd/system/wardogs-status.service.d /usr/local/lib/wardogs-status /run/wardogs-status
if [[ -n "$OUT_DIR" ]]; then rm -f "$OUT_DIR/status.json"; fi
systemctl daemon-reload
userdel wdstatus 2>/dev/null || true
echo "UNINSTALL OK"
