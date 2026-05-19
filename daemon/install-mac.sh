#!/usr/bin/env bash
# install-mac.sh — Install the Claude Usage Daemon as a macOS LaunchAgent
# Usage: ./install-mac.sh

set -euo pipefail

DAEMON_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$DAEMON_DIR")"
DAEMON_SCRIPT="$DAEMON_DIR/claude_usage_daemon.py"
VENV_DIR="$DAEMON_DIR/.venv"
PLIST_TEMPLATE="$DAEMON_DIR/com.user.claude-usage-daemon.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.user.claude-usage-daemon.plist"
LOG_DIR="$HOME/Library/Logs"
LOG_OUT="$LOG_DIR/claude-usage-daemon.out.log"
LOG_ERR="$LOG_DIR/claude-usage-daemon.err.log"

echo "=== Claude Usage Daemon — macOS installer ==="
echo ""

# ── 1. Python venv ────────────────────────────────────────────
echo "→ Creating Python venv in daemon/.venv ..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet bleak httpx
PYTHON_BIN="$VENV_DIR/bin/python3"
echo "  ✓ venv ready: $PYTHON_BIN"
echo ""

# ── 2. Render plist ───────────────────────────────────────────
echo "→ Writing LaunchAgent plist to $PLIST_DEST ..."
mkdir -p "$HOME/Library/LaunchAgents"
sed \
  -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
  -e "s|__DAEMON_PATH__|$DAEMON_SCRIPT|g" \
  -e "s|__REPO_DIR__|$REPO_DIR|g" \
  -e "s|__LOG_OUT__|$LOG_OUT|g" \
  -e "s|__LOG_ERR__|$LOG_ERR|g" \
  -e "s|__HOME__|$HOME|g" \
  "$PLIST_TEMPLATE" > "$PLIST_DEST"
echo "  ✓ plist written"
echo ""

# ── 3. First interactive run (grants Bluetooth permission) ────
echo "→ Running daemon once interactively so macOS can prompt for Bluetooth access."
echo "  Allow Bluetooth when the dialog appears, then press Ctrl+C to stop."
echo "  (The LaunchAgent will take over after that.)"
echo ""
read -r -p "  Press Enter to start the interactive run..."
"$PYTHON_BIN" "$DAEMON_SCRIPT" || true
echo ""

# ── 4. Load LaunchAgent ───────────────────────────────────────
echo "→ Loading LaunchAgent ..."
# Unload first in case it was already loaded
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"
echo "  ✓ Daemon loaded and will start automatically at login."
echo ""

# ── 5. Summary ────────────────────────────────────────────────
echo "=== Done! ==="
echo ""
echo "Useful commands:"
echo "  Check status:  launchctl list | grep claude-usage"
echo "  Live logs:     tail -F $LOG_OUT"
echo "  Stop:          launchctl unload $PLIST_DEST"
echo "  Start:         launchctl load -w $PLIST_DEST"
echo ""
