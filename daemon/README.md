# daemon

Python daemon that polls Claude API usage and sends it to the ESP32 over BLE.
Also serves the web control page on `http://localhost:8741/control.html`.

---

## Files

| File | Purpose |
|---|---|
| `claude_usage_daemon.template.py` | **Start here.** Copy this to `claude_usage_daemon.py` and run it. |
| `install-mac.sh` | One-command installer — sets up a venv and registers a macOS LaunchAgent so the daemon starts automatically at login. |
| `com.user.claude-usage-daemon.plist` | LaunchAgent config template used by `install-mac.sh`. Do not edit or run directly. |
| `scan_all.py` | BLE debug tool — scans for nearby devices and flags your ESP32. Use if the daemon can't find the board. |

---

## Quick start (manual)

```bash
cd daemon
python3 -m venv .venv
source .venv/bin/activate
pip install bleak httpx
cp claude_usage_daemon.template.py claude_usage_daemon.py
python3 claude_usage_daemon.py
```

Requires Claude Code CLI signed in (`claude login`).  
The control page opens automatically at `http://localhost:8741/control.html`.

**Stop / restart:**
```bash
lsof -ti:8741 | xargs kill -9 2>/dev/null; pkill -f claude_usage_daemon.py 2>/dev/null; sleep 0.5
source .venv/bin/activate && python3 claude_usage_daemon.py
```

---

## Auto-start at login (recommended)

Run the installer once:

```bash
chmod +x install-mac.sh
./install-mac.sh
```

It will:
1. Create a venv and install dependencies
2. Run the daemon once interactively so macOS can prompt for Bluetooth permission
3. Install and load the LaunchAgent — daemon starts automatically at every login

**Useful commands after installing:**
```bash
launchctl list | grep claude-usage          # check it's running
tail -F ~/Library/Logs/claude-usage-daemon.out.log  # live logs
launchctl unload ~/Library/LaunchAgents/com.user.claude-usage-daemon.plist  # stop
launchctl load -w ~/Library/LaunchAgents/com.user.claude-usage-daemon.plist # start
```

---

## BLE troubleshooting

If the daemon prints `Device not found` repeatedly:

```bash
source .venv/bin/activate
python3 scan_all.py
```

This scans for 10 seconds and lists all nearby BLE devices. Your ESP32 will be marked with `*` if it's advertising. If it doesn't appear:
- Make sure the firmware is flashed and the board is powered
- Check **System Settings → Privacy & Security → Bluetooth** — Terminal must be allowed
- Try moving the ESP32 closer to your Mac
