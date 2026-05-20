# clawd-meter

A physical Claude usage monitor built on ESP32-C3 Super Mini. Displays your Claude rate-limit stats and an animated pixel-art mascot on a 1.54" TFT screen, connected to a Mac daemon over BLE. Inspired by [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) & [clawd-mochi](https://github.com/yousifamanuel/clawd-mochi). Get the [3D case file here](https://makerworld.com/en/models/2559505-clawd-mochi-physical-claude-code-mascot#profileId-2820000).

```
sprite animation (30s) → "claude is Cogitating..." (5s) → usage % + bars (5s) → repeat
```

Animations auto-select based on usage rate and time of day. No API calls midnight–7 am (UTC+8).

---

## Hardware

| Part | Notes |
|---|---|
| ESP32-C3 Super Mini (or any ESP32) | Tested on ESP32-C3 Super Mini |
| 240×240 ST7789 TFT | SPI, 12px cell = 240px full-screen sprite |

**Pin wiring (edit top of `.ino` to match your board):**

```
VCC → 3V3
GND → GND
CS  → GPIO 4
DC  → GPIO 1
RST → GPIO 2
BLK → GPIO 3
SCK → GPIO 8
SDA → GPIO 10
```

---

## Setup

### 1. (Optional) Regenerate sprite data

The pixel-art frame data is already embedded in `clawd_meter.ino` — **you can skip this step** and go straight to flashing.

If you want to regenerate it (e.g. after modifying `tools/claudepix_data/`):

```bash
cd tools
python3 gen_sprites.py --inject
```

This re-injects the sprite arrays directly into `clawd_meter.ino`. The JSON source files live in `tools/claudepix_data/` and were originally sourced from [claudepix.vercel.app](https://claudepix.vercel.app) by [@amaanbuilds](https://github.com/amaanbuilds).

### 2. Flash firmware

Open `clawd_meter/clawd_meter.ino` in Arduino IDE.

Add the ESP32 board package — paste this into **Preferences → Additional Board Manager URLs**:
```
https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
```

Install **esp32 by Espressif Systems** via Board Manager.

Install these libraries via Library Manager:
- **NimBLE-Arduino**
- **Adafruit ST7789 & ST7735**
- **ArduinoJson**

Set the following under **Tools**:

| Setting | Value |
|---|---|
| Board | ESP32C3 Dev Module |
| USB CDC On Boot | Enabled ← important! |
| CPU Frequency | 160 MHz |
| Flash Mode | DIO |
| JTAG Adapter | Integrated USB JTAG |
| Upload Speed | 921600 |

Select your board → **Compile** → **Upload**.

> **Can't find the COM port?** Hold BOOT → press & release RESET → release BOOT, then try uploading again.

### 3. Run the daemon

Requires macOS + Claude Code CLI installed and signed in (`claude login`).

```bash
cd daemon
python3 -m venv .venv
source .venv/bin/activate
pip install bleak httpx
cp claude_usage_daemon.template.py claude_usage_daemon.py
python3 claude_usage_daemon.py
```

Control page opens automatically at `http://localhost:8741/control.html`.

**Stop / restart:**
```bash
lsof -ti:8741 | xargs kill -9 2>/dev/null; pkill -f claude_usage_daemon.py 2>/dev/null; sleep 0.5
cd daemon && source .venv/bin/activate && python3 claude_usage_daemon.py
```

See [`daemon/README.md`](daemon/README.md) for auto-start setup and BLE troubleshooting.

### 4. iOS Scriptable widget (optional)

1. Install [Scriptable](https://apps.apple.com/app/scriptable/id1405459188)
2. Create a new script and paste `scriptable/clawd_meter.js`
3. Update the first line with your Mac's local IP:
```javascript
const DAEMON = "http://YOUR-MAC-IP:8741"
```
Find your IP: `ifconfig | grep "inet " | grep -v 127.0.0.1`

4. Run once in-app to test, then add a Scriptable widget to your home screen.

Tapping the widget opens a full animation control UI.

---

## How API polling works

The daemon never stores your Claude credentials — it reads them live from your Mac each poll.

**Token source (tried in order):**
1. macOS Keychain — `Claude Code-credentials` entry (created by `claude login`)
2. `~/.claude/.credentials.json` — file-based fallback
3. Claude Desktop app — decrypts the token from `~/Library/Application Support/Claude/config.json`

**Poll cycle:**
1. Every **5 minutes**, the daemon makes a minimal API call to `api.anthropic.com/v1/messages` with `max_tokens: 1` and `claude-haiku` — just enough to get response headers back, consuming essentially no tokens.
2. The response headers contain your rate-limit utilisation:
   - `anthropic-ratelimit-unified-5h-utilization` — rolling 5-hour window (%)
   - `anthropic-ratelimit-unified-7d-utilization` — rolling 7-day window (%)
   - `anthropic-ratelimit-unified-5h-reset` — Unix timestamp of next 5h reset
   - `anthropic-ratelimit-unified-7d-reset` — Unix timestamp of next 7d reset
3. These values are packed into a small JSON payload and sent to the ESP32 over BLE.
4. If the access token is expired, the daemon automatically refreshes it using your stored refresh token — no manual re-login needed.

**Quiet hours:** No API calls are made between **00:00–07:00 UTC+8** to avoid unnecessary background activity while you sleep.

**Auto-animation:** The daemon also tracks a ring buffer of the last 6 usage samples (spanning at least 4 minutes) to calculate a usage growth rate. This rate drives the animation group displayed on the sprite screen — idle, low, medium, or high activity.

---

## Display behaviour

| Mode | Duration | Description |
|---|---|---|
| Sprite | 30 s | Animated mascot, group selected by usage rate |
| Word | 5 s | "claude is *Cogitating*..." random -ing word |
| Usage | 5 s | 5h % + weekly % bars, reset countdowns, battery icon |

**Auto animation groups (by usage growth rate):**

| Group | Rate | Animations |
|---|---|---|
| 0 — idle | no growth | Breathe / Blink / Look Around (or time-of-day override) |
| 1 — low | < 0.10 %/min | Wink / Surprise / Think |
| 2 — medium | < 0.20 %/min | Sleep / Sway / Coding |
| 3 — high | ≥ 0.20 %/min | Bounce / Bounce DJ / Sway DJ / DJ Mix |

**Time-of-day idle overrides (UTC+8):**

| Hours | Animation |
|---|---|
| 00:00 – 05:59 | Sleep |
| 06:00 – 09:59 | Coding |
| 10:00 – 17:59 | Normal idle cycle |
| 18:00 – 22:59 | Bounce |

**Reset celebration:** when your 5h usage drops from >30% to <10% (limit reset), plays DJ Mix for 12 seconds.

---

## Credits

- **Pixel-art Clawd animation** by [@amaanbuilds](https://github.com/amaanbuilds), sourced from [claudepix.vercel.app](https://claudepix.vercel.app). Frame data and palettes converted by `tools/gen_sprites.py`.
- **Clawdmeter** by [HermannBjorgvin](https://github.com/HermannBjorgvin/Clawdmeter) — original BLE architecture, sprite system, daemon structure, and tooling that this project builds on.
- **clawd-mochi** by [yousifamanuel](https://github.com/yousifamanuel/clawd-mochi) — additional reference implementation.

**Added in this fork:**
- Word screen with dynamic font sizing
- Restyled usage screen — white values, green bars, battery icon, reset countdown
- 30 s / 5 s / 5 s display cycle
- Reset celebration animation
- Time-of-day idle animation selection
- Quiet hours (no API polling midnight–7 am UTC+8)
- iOS Scriptable widget
- Claude Desktop OAuth token fallback
- Animation restore and ring-buffer bug fixes

---

## ⚠️ Licensing

**The software code** (daemon, firmware logic, Scriptable widget) is shared for personal and educational use.

This project uses the **Clawd mascot**, which is Anthropic's copyrighted character. Pixel-art frames were created by [@amaanbuilds](https://github.com/amaanbuilds) and are included here solely for local use — they were originally sourced from [claudepix.vercel.app](https://claudepix.vercel.app).

The upstream project [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) carries this explicit warning:

> *This repository uses Anthropic brand assets and the copyrighted Clawd mascot. Even though the code is non-proprietary, it is not licensed under a copyleft license due to inclusion of proprietary fonts and copyrighted assets. Please be aware of this if you fork or copy the code.*

The same applies here. **This project is not affiliated with or endorsed by Anthropic.**
