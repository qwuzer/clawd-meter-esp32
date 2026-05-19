#!/usr/bin/env python3
"""Claude Usage Tracker Daemon (BLE)

Polls Claude API rate-limit headers and sends usage data to the
ESP32 "Claude Controller" peripheral over BLE (custom GATT service).
Also serves control.html on localhost and forwards webpage commands over BLE.

Setup:
    python3 -m venv .venv
    source .venv/bin/activate
    pip install bleak httpx
    python3 claude_usage_daemon.py

Requires Claude Code CLI installed and signed in:
    claude login
"""

from __future__ import annotations  # Python 3.9 compatibility

import asyncio
import datetime
import getpass
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import time
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

try:
    import httpx
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:
    print("Missing dependencies. Run:  pip3 install bleak httpx")
    raise SystemExit(1)

# ── BLE ───────────────────────────────────────────────────────
DEVICE_NAME   = "Claude Controller"
SERVICE_UUID  = "4c41555a-4465-7669-6365-000000000001"
RX_CHAR_UUID  = "4c41555a-4465-7669-6365-000000000002"
REQ_CHAR_UUID = "4c41555a-4465-7669-6365-000000000004"

# ── Tuneable settings ─────────────────────────────────────────
POLL_INTERVAL = 300   # seconds between API polls (5 min recommended)
TICK          = 5     # BLE event loop tick interval (seconds)
SCAN_TIMEOUT  = 8.0   # BLE scan timeout (seconds)

# ── Credentials ───────────────────────────────────────────────
# No changes needed here — credentials are read automatically from:
#   macOS keychain  ("Claude Code-credentials")
#   ~/.claude/.credentials.json  (fallback)
#   ~/Library/Application Support/Claude/config.json  (Claude Desktop fallback)
# Run `claude login` in your terminal if the daemon reports "No token".
KEYCHAIN_SERVICE = "Claude Code-credentials"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
SAVED_ADDR_FILE  = Path.home() / ".config" / "claude-usage-monitor" / "ble-address"

# ── API ───────────────────────────────────────────────────────
# OAUTH_CLIENT_ID below is Anthropic's public Claude Code client ID —
# it is embedded in the Claude Code binary and is not a personal secret.
API_URL = "https://api.anthropic.com/v1/messages"
API_HEADERS_TEMPLATE = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta":    "oauth-2025-04-20",
    "Content-Type":      "application/json",
    "User-Agent":        "claude-code/2.1.5",
}
API_BODY = {
    "model":      "claude-haiku-4-5-20251001",
    "max_tokens": 1,   # minimal — we only need the response headers
    "messages":   [{"role": "user", "content": "hi"}],
}

# ── Control server ────────────────────────────────────────────
CONTROL_HTML  = Path(__file__).parent.parent / "control.html"
CONTROL_PORT  = 8741

# Commands from webpage → BLE (thread-safe)
_cmd_queue:   asyncio.Queue = None   # set in main()
_cmd_loop:    asyncio.AbstractEventLoop = None
_ble_status   = {"connected": False}
_display_state: dict = None


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Quiet hours ───────────────────────────────────────────────
# No API calls from 00:00 (midnight) to 07:00, UTC+8.
_TZ_UTC8 = datetime.timezone(datetime.timedelta(hours=8))

def is_quiet_hours() -> bool:
    h = datetime.datetime.now(_TZ_UTC8).hour
    return h < 7


# ── OAuth refresh ─────────────────────────────────────────────
OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
KEYCHAIN_ACCOUNT = getpass.getuser()

_last_refresh_attempt: float = 0.0
REFRESH_COOLDOWN = 300.0  # don't retry refresh more than once per 5 min

# ── Claude Desktop token fallback ─────────────────────────────
# When running via the Claude Desktop app, credentials are stored encrypted
# in config.json using Electron safeStorage (AES-128-CBC v10).
DESKTOP_CONFIG_PATH  = Path.home() / "Library" / "Application Support" / "Claude" / "config.json"
SAFE_STORAGE_SERVICE = "Claude Safe Storage"
SAFE_STORAGE_ACCOUNT = "Claude"


def _decrypt_safe_storage_v10(ciphertext: bytes, password: str) -> bytes | None:
    """Decrypt an Electron safeStorage v10 blob (AES-128-CBC, PBKDF2-SHA1 key)."""
    import hashlib
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, dklen=16)
    iv  = b" " * 16
    try:
        result = subprocess.run(
            ["openssl", "enc", "-aes-128-cbc", "-d", "-nosalt",
             "-K", key.hex(), "-iv", iv.hex()],
            input=ciphertext, capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout
        # Strip PKCS7 padding
        pad = raw[-1]
        if 1 <= pad <= 16:
            raw = raw[:-pad]
        return raw
    except Exception:
        return None


def _read_desktop_token_cache() -> dict | None:
    """Read and decrypt the Claude Desktop OAuth token cache from config.json."""
    if not DESKTOP_CONFIG_PATH.exists():
        return None
    try:
        config = json.loads(DESKTOP_CONFIG_PATH.read_text())
        enc_b64 = config.get("oauth:tokenCache")
        if not enc_b64 or not isinstance(enc_b64, str):
            return None
        raw = __import__("base64").b64decode(enc_b64)
        if not raw.startswith(b"v10"):
            return None
    except Exception:
        return None

    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", SAFE_STORAGE_SERVICE, "-a", SAFE_STORAGE_ACCOUNT, "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        password = out.stdout.strip()
    except Exception as e:
        log(f"Desktop safe-storage key unavailable: {e}")
        return None

    plaintext = _decrypt_safe_storage_v10(raw[3:], password)
    if not plaintext:
        log("Desktop token cache decryption failed")
        return None
    try:
        return json.loads(plaintext.decode())
    except Exception:
        return None


def _read_keychain_blob() -> str | None:
    """Return raw keychain blob (JSON string)."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired) as e:
        log(f"Keychain read error: {e}")
        return None


def _write_keychain_blob(blob: str) -> bool:
    """Overwrite the keychain entry with a new JSON blob."""
    try:
        subprocess.run(
            ["security", "add-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
             "-w", blob, "-U"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired) as e:
        log(f"Keychain write error: {e}")
        return False


def _get_refresh_token() -> str | None:
    """Extract refresh token from keychain blob, falling back to Desktop cache."""
    blob = _read_keychain_blob()
    if blob:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data.get("refreshToken"), str):
            return data["refreshToken"]
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("refreshToken"), str):
                return v["refreshToken"]
    # Fallback: Claude Desktop encrypted token cache
    cache = _read_desktop_token_cache()
    if cache:
        if isinstance(cache.get("refreshToken"), str):
            return cache["refreshToken"]
        for v in cache.values():
            if isinstance(v, dict) and isinstance(v.get("refreshToken"), str):
                return v["refreshToken"]
    return None


async def _refresh_access_token() -> str | None:
    """
    Call platform.claude.com OAuth refresh endpoint.
    On success, update keychain and return new access token.
    Returns None on failure.
    """
    global _last_refresh_attempt
    now = time.time()
    if now - _last_refresh_attempt < REFRESH_COOLDOWN:
        log("Token refresh on cooldown, skipping")
        return None
    _last_refresh_attempt = now

    refresh_token = _get_refresh_token()
    if not refresh_token:
        log("No refresh token available — run: claude login")
        return None

    log("Refreshing OAuth access token...")
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(
                OAUTH_TOKEN_URL,
                json={
                    "grant_type":    "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id":     OAUTH_CLIENT_ID,
                },
            )
    except httpx.HTTPError as e:
        log(f"Token refresh request failed: {e}")
        return None

    if resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        log(f"Token refresh failed ({resp.status_code}): {err}")
        if isinstance(err, dict) and err.get("error") == "invalid_grant":
            log("Refresh token is invalid/expired — run: claude login")
        return None

    data = resp.json()
    new_access  = data.get("access_token")
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in  = data.get("expires_in", 3600)

    if not new_access:
        log(f"Token refresh: unexpected response: {data}")
        return None

    # Update the keychain blob
    blob = _read_keychain_blob()
    try:
        blob_data = json.loads(blob) if blob else {}
    except json.JSONDecodeError:
        blob_data = {}

    new_creds = {
        "accessToken":  new_access,
        "refreshToken": new_refresh,
        "expiresAt":    int((time.time() + expires_in) * 1000),
    }
    # Preserve extra fields (scopes, subscriptionType, etc.)
    # Find the nested oauth key if present
    nested_key = None
    for k, v in blob_data.items():
        if isinstance(v, dict) and "accessToken" in v:
            nested_key = k
            break

    if nested_key:
        blob_data[nested_key].update(new_creds)
    elif "accessToken" in blob_data:
        blob_data.update(new_creds)
    else:
        blob_data = new_creds

    if _write_keychain_blob(json.dumps(blob_data)):
        log("Token refreshed and keychain updated ✓")
        return new_access
    return None


# ── Token reading (identical to reference) ────────────────────

def _extract_access_token(blob: str) -> str | None:
    blob = blob.strip()
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        if isinstance(data.get("accessToken"), str):
            return data["accessToken"]
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                return v["accessToken"]
    m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', blob)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-.~+/=]{20,}", blob):
        return blob
    return None


def _read_token_keychain() -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except subprocess.CalledProcessError as e:
        log(f"Keychain read failed (rc={e.returncode}): {e.stderr.strip()}")
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"Keychain access error: {e}")
        return None
    return _extract_access_token(out.stdout)


def _read_token_file() -> str | None:
    try:
        raw = CREDENTIALS_PATH.read_text()
    except OSError as e:
        log(f"Error reading credentials: {e}")
        return None
    return _extract_access_token(raw)


def read_token() -> str | None:
    if sys.platform == "darwin":
        token = _read_token_keychain()
        if token:
            return token
        # Fallback: Claude Desktop app stores tokens encrypted in config.json
        cache = _read_desktop_token_cache()
        if cache:
            log("Using token from Claude Desktop config")
            return _extract_access_token(json.dumps(cache))
        return None
    return _read_token_file()


def _get_expires_at_ms() -> int | None:
    """Return expiresAt in milliseconds, checking keychain then Desktop cache."""
    blob = _read_keychain_blob()
    if blob:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data.get("expiresAt"), (int, float)):
            return int(data["expiresAt"])
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("expiresAt"), (int, float)):
                return int(v["expiresAt"])
    # Fallback: Claude Desktop encrypted token cache
    cache = _read_desktop_token_cache()
    if cache:
        if isinstance(cache.get("expiresAt"), (int, float)):
            return int(cache["expiresAt"])
        for v in cache.values():
            if isinstance(v, dict) and isinstance(v.get("expiresAt"), (int, float)):
                return int(v["expiresAt"])
    return None


async def get_valid_token() -> str | None:
    """
    Return a valid access token, proactively refreshing if it's
    within 10 minutes of expiry (or already expired).
    """
    expires_ms = _get_expires_at_ms()
    now_ms     = int(time.time() * 1000)
    margin_ms  = 10 * 60 * 1000  # 10 minutes

    if expires_ms is not None and (expires_ms - now_ms) < margin_ms:
        mins_left = (expires_ms - now_ms) / 60000
        log(f"Token expires in {mins_left:.1f} min — proactively refreshing...")
        new_token = await _refresh_access_token()
        if new_token:
            return new_token
        # Fall through and use whatever is in the keychain
        log("Proactive refresh failed; trying existing token anyway")

    return read_token()


# ── Address cache (same as reference) ─────────────────────────

def load_cached_address() -> str | None:
    if not SAVED_ADDR_FILE.exists():
        return None
    addr = SAVED_ADDR_FILE.read_text().strip()
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", addr) or re.fullmatch(
        r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}", addr
    ):
        return addr
    log("Cached address malformed, discarding")
    SAVED_ADDR_FILE.unlink(missing_ok=True)
    return None


def save_address(addr: str) -> None:
    SAVED_ADDR_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAVED_ADDR_FILE.write_text(addr)


# ── BLE scan (same as reference) ──────────────────────────────

async def scan_for_device() -> str | None:
    log(f"Scanning for '{DEVICE_NAME}' ({SCAN_TIMEOUT}s)...")
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
    for d in devices:
        if d.name == DEVICE_NAME:
            log(f"Found: {d.address}")
            return d.address
    return None


# ── API poll (same as reference) ──────────────────────────────

async def poll_api(token: str) -> dict | None:
    headers = dict(API_HEADERS_TEMPLATE)
    headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(API_URL, headers=headers, json=API_BODY)
    except httpx.HTTPError as e:
        log(f"API call failed: {e}")
        return None

    log(f"API status: {resp.status_code}")
    if resp.status_code == 401:
        return {"_needs_refresh": True}  # caller will handle refresh

    # Only return a payload when the unified rate-limit headers are actually
    # present. If they're absent (some account types / non-200 responses),
    # returning zeros would call usageRateSample(0) on the device and wipe
    # the rate-group ring buffer, breaking auto-animation for 4+ minutes.
    util_5h_raw = resp.headers.get("anthropic-ratelimit-unified-5h-utilization")
    util_7d_raw = resp.headers.get("anthropic-ratelimit-unified-7d-utilization")
    if util_5h_raw is None and util_7d_raw is None:
        log(f"Rate-limit utilization headers absent (HTTP {resp.status_code}) — skipping usage update")
        return None

    def hdr(name: str, default: str = "0") -> str:
        return resp.headers.get(name, default)

    now = time.time()

    def reset_minutes(reset_ts: str) -> int:
        try:
            r = float(reset_ts)
        except ValueError:
            return 0
        mins = (r - now) / 60.0
        return int(round(mins)) if mins > 0 else 0

    def pct(util: str) -> int:
        try:
            return int(round(float(util) * 100))
        except ValueError:
            return 0

    return {
        "s":  pct(hdr("anthropic-ratelimit-unified-5h-utilization")),
        "sr": reset_minutes(hdr("anthropic-ratelimit-unified-5h-reset")),
        "w":  pct(hdr("anthropic-ratelimit-unified-7d-utilization")),
        "wr": reset_minutes(hdr("anthropic-ratelimit-unified-7d-reset")),
        "st": hdr("anthropic-ratelimit-unified-5h-status", "unknown"),
        "h":  time.localtime().tm_hour,
        "ok": True,
    }


# ── Session (same as reference + command forwarding) ──────────

class Session:
    def __init__(self, client: BleakClient) -> None:
        self.client = client
        self.refresh_requested = asyncio.Event()

    def _on_refresh(self, _char, _data: bytearray) -> None:
        log("Refresh requested by device")
        self.refresh_requested.set()

    async def setup_refresh_subscription(self) -> None:
        try:
            await self.client.start_notify(REQ_CHAR_UUID, self._on_refresh)
        except (BleakError, ValueError) as e:
            log(f"Refresh subscription unavailable: {e}")

    async def write_payload(self, payload: dict) -> bool:
        data = json.dumps(payload, separators=(",", ":")).encode()
        log(f"Sending: {data.decode()}")
        try:
            await self.client.write_gatt_char(RX_CHAR_UUID, data, response=False)
            return True
        except BleakError as e:
            log(f"Write failed: {e}")
            return False


# ── connect_and_run (reference structure + command task) ───────

async def connect_and_run(address: str, stop_event: asyncio.Event) -> bool:
    log(f"Connecting to {address}...")
    client = BleakClient(address)
    try:
        await client.connect()
    except (BleakError, asyncio.TimeoutError) as e:
        log(f"Connection failed: {e}")
        return False

    if not client.is_connected:
        log("Connection failed (no error but not connected)")
        return False

    log("Connected")
    _ble_status["connected"] = True
    session = Session(client)
    await session.setup_refresh_subscription()

    last_poll  = 0.0
    used_ok    = False
    done       = asyncio.Event()

    async def command_task() -> None:
        global _display_state
        while not done.is_set():
            try:
                cmd = await asyncio.wait_for(_cmd_queue.get(), timeout=0.2)
                _display_state = cmd
                await session.write_payload(cmd)
                log(f"Command: {cmd}")
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                log(f"Command error: {exc}")

    async def poll_task() -> None:
        nonlocal last_poll, used_ok
        while client.is_connected and not stop_event.is_set():
            now     = time.time()
            elapsed = now - last_poll
            if session.refresh_requested.is_set() or elapsed >= POLL_INTERVAL:
                session.refresh_requested.clear()
                last_poll = time.time()  # Always advance so we don't spam
                if is_quiet_hours():
                    log("Quiet hours (00:00–07:00 UTC+8) — skipping poll")
                    continue
                token = await get_valid_token()
                if not token:
                    log("No token; skipping poll")
                else:
                    payload = await poll_api(token)
                    # Auto-refresh on 401
                    if isinstance(payload, dict) and payload.get("_needs_refresh"):
                        log("Access token expired — attempting auto-refresh...")
                        new_token = await _refresh_access_token()
                        if new_token:
                            payload = await poll_api(new_token)
                            if isinstance(payload, dict) and payload.get("_needs_refresh"):
                                log("Still 401 after refresh — run: claude logout && claude login")
                                payload = None
                        else:
                            log("Auto-refresh failed — run: claude logout && claude login")
                            payload = None
                    if payload is not None:
                        if await session.write_payload(payload):
                            used_ok = True
                            # Re-send locked animation after usage screen takes over.
                            # Don't re-send auto mode (anim=-1): re-sending it resets
                            # animSubIdx/groupRotateDue on the device, breaking the
                            # within-group rotation cycle. The device manages auto mode
                            # from its own ring buffer without needing a nudge.
                            if _display_state and _display_state.get("anim", -1) >= 0:
                                await asyncio.sleep(0.05)
                                await session.write_payload(_display_state)
            try:
                await asyncio.wait_for(session.refresh_requested.wait(), timeout=TICK)
            except asyncio.TimeoutError:
                pass

    try:
        await asyncio.gather(command_task(), poll_task())
    finally:
        done.set()
        try:
            await client.disconnect()
        except BleakError:
            pass
        _ble_status["connected"] = False

    log("Device disconnected" if not stop_event.is_set() else "Stopping")
    return used_ok


# ── Control server ────────────────────────────────────────────

def start_control_server() -> None:
    if not CONTROL_HTML.exists():
        return

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(CONTROL_HTML.parent), **kw)

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/status":
                body = json.dumps(_ble_status).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

        def do_POST(self):
            if self.path == "/command":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    cmd = json.loads(self.rfile.read(length))
                    if _cmd_loop and _cmd_queue:
                        _cmd_loop.call_soon_threadsafe(_cmd_queue.put_nowait, cmd)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                except Exception:
                    self.send_response(400)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

    HTTPServer.allow_reuse_address = True
    httpd = HTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    url = f"http://localhost:{CONTROL_PORT}/control.html"
    print(f"[control] {url}")
    webbrowser.open(url)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


# ── Main (same structure as reference) ───────────────────────

async def main() -> None:
    global _cmd_queue, _cmd_loop
    _cmd_queue = asyncio.Queue()
    _cmd_loop  = asyncio.get_running_loop()

    stop_event = asyncio.Event()
    loop       = asyncio.get_running_loop()

    def _stop(*_args: object) -> None:
        log("Daemon stopping")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, _stop)

    log("=== Claude Usage Tracker Daemon ===")
    log(f"Poll interval: {POLL_INTERVAL}s")
    start_control_server()

    backoff = 1
    while not stop_event.is_set():
        address = load_cached_address()
        if not address:
            address = await scan_for_device()
            if address:
                save_address(address)
            else:
                log(f"Device not found, retrying in {backoff}s...")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)
                continue

        ok = await connect_and_run(address, stop_event)
        if not ok:
            log("Invalidating cached address")
            SAVED_ADDR_FILE.unlink(missing_ok=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60)
        else:
            backoff = 1


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
