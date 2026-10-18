#!/usr/bin/env bash
# Install or update the WARDOGS status collector.
#
#   sudo bash deploy/install.sh /var/www/example/wardogs/data
#
# The argument is the folder your website serves the status file from (it becomes
# <folder>/status.json). On later runs you can leave it out to keep the current folder.
# Safe to run again: it only replaces the collector and its own unit files.
# It never touches the vote bot, its settings, or the rest of the website.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo bash $0 <public data folder>"; exit 1; }
cd "$(dirname "$0")/.."
DROPIN=/etc/systemd/system/wardogs-status.service.d/output.conf

OUT_DIR=${1:-}
if [[ -z "$OUT_DIR" && -f $DROPIN ]]; then
  OUT_DIR=$(sed -n 's/^ReadWritePaths=//p' "$DROPIN" | head -1)
  echo "keeping the current folder: $OUT_DIR"
fi
[[ -n "$OUT_DIR" ]] || { echo "usage: sudo bash $0 /path/to/website/wardogs/data"; exit 1; }
[[ "$OUT_DIR" == /* && "$OUT_DIR" != *" "* && "$OUT_DIR" != *..* ]] || { echo "use an absolute path without spaces"; exit 1; }
OUT_DIR=${OUT_DIR%/}
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "Python 3.9 or newer is required"; exit 1; }

getent group wdhealth >/dev/null || groupadd --system wdhealth
id wdstatus >/dev/null 2>&1 || useradd --system --user-group --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin --groups wdhealth wdstatus
install -o root -g root -m 644 tmpfiles.d/wardogs-health.conf /etc/tmpfiles.d/wardogs-health.conf
systemd-tmpfiles --create /etc/tmpfiles.d/wardogs-health.conf

install -d -o root -g root -m 755 /usr/local/lib/wardogs-status
install -o root -g root -m 755 collector/wardogs_status_collector.py /usr/local/lib/wardogs-status/
install -d -o wdstatus -g wdstatus -m 755 "$OUT_DIR"
install -o root -g root -m 644 systemd/wardogs-status.service systemd/wardogs-status.timer /etc/systemd/system/
install -d -o root -g root -m 755 "$(dirname $DROPIN)"
printf '# Written by deploy/install.sh: where the public status file goes.\n[Service]\nEnvironment=WDS_OUTPUT=%s/status.json\nReadWritePaths=%s\n' \
  "$OUT_DIR" "$OUT_DIR" > "$DROPIN"
chmod 644 "$DROPIN"
systemctl daemon-reload
systemd-analyze verify wardogs-status.service wardogs-status.timer

systemctl start wardogs-status.service || true
journalctl -u wardogs-status.service -n 3 --no-pager -o cat || true
python3 -m json.tool "$OUT_DIR/status.json" | head -20
systemctl enable --now wardogs-status.timer
systemctl list-timers wardogs-status.timer --no-pager | head -3

if [[ ! -f /run/wardogs-health/health.json ]]; then
  echo
  echo "Note: no bot health snapshot yet, so the bot rows show Unknown. Enable it in the vote bot with"
  echo "its deploy/health.conf drop-in (see README, 'Connect the vote bot')."
fi
echo "INSTALL OK: $OUT_DIR/status.json"
