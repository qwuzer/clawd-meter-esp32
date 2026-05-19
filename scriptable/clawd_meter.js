// Clawd Meter — iOS Scriptable Widget
// ─────────────────────────────────────
// 1. Install Scriptable from the App Store.
// 2. Create a new script and paste this file.
// 3. Replace YOUR-MAC-IP with your Mac's local IP address.
//    Find it with:  ifconfig | grep "inet " | grep -v 127.0.0.1
// 4. Run once in-app to test, then add a Scriptable widget to your home screen.
//    Tapping the widget opens the full animation control UI.

const DAEMON = "http://YOUR-MAC-IP:8741"   // ← fill in your Mac's local IP

// ── Animation index → display name ───────────────────────────
const ANIM_NAMES = [
  "Breathe",     // 0  (idle)
  "Blink",       // 1
  "Look Around", // 2
  "Wink",        // 3  (expression)
  "Surprise",    // 4
  "Sleep",       // 5
  "Bounce",      // 6  (dance)
  "Sway",        // 7
  "Coding",      // 8  (work)
  "Think",       // 9
  "Bounce DJ",   // 10 (dance+)
  "Sway DJ",     // 11
  "DJ Mix",      // 12
]

// ── Network helpers ───────────────────────────────────────────

async function getStatus() {
  try {
    const req = new Request(`${DAEMON}/status`)
    req.timeoutInterval = 4
    return await req.loadJSON()
  } catch (_) {
    return { connected: false }
  }
}

async function send(obj) {
  try {
    const req = new Request(`${DAEMON}/command`)
    req.method = "POST"
    req.headers = { "Content-Type": "application/json" }
    req.body = JSON.stringify(obj)
    req.timeoutInterval = 4
    await req.loadString()
    return true
  } catch (_) {
    return false
  }
}

// ── Small home-screen widget ──────────────────────────────────

async function buildWidget() {
  const status = await getStatus()
  const w = new ListWidget()
  w.backgroundColor = new Color("#1f1f1e")
  w.url = `${DAEMON}/control.html`

  const title = w.addText("Clawd Meter")
  title.font = Font.boldSystemFont(13)
  title.textColor = new Color("#d97757")

  w.addSpacer(6)

  const dot = status.connected ? "🟢" : "🔴"
  const label = status.connected ? "Connected" : "Daemon offline"
  const sub = w.addText(`${dot} ${label}`)
  sub.font = Font.systemFont(11)
  sub.textColor = new Color("#b0aea5")

  w.addSpacer()

  const hint = w.addText("Tap to control →")
  hint.font = Font.systemFont(10)
  hint.textColor = new Color("#555")

  return w
}

// ── Full-screen interactive UI ────────────────────────────────

async function showUI() {
  const status = await getStatus()
  const table = new UITable()
  table.showSeparators = true

  // ── Header row ──
  const hdr = new UITableRow()
  hdr.isHeader = true
  hdr.backgroundColor = new Color("#000")
  const hdrCell = hdr.addText("Clawd Meter", status.connected ? "🟢 Connected" : "🔴 Offline")
  hdrCell.titleColor = new Color("#d97757")
  hdrCell.subtitleColor = new Color("#b0aea5")
  table.addRow(hdr)

  // ── Section helper ──
  function sectionRow(title) {
    const r = new UITableRow()
    r.isHeader = true
    r.backgroundColor = new Color("#111")
    const c = r.addText(title)
    c.titleColor = new Color("#888")
    c.titleFont = Font.systemFont(11)
    table.addRow(r)
  }

  // ── Animation button helper ──
  function animRow(label, animIdx) {
    const r = new UITableRow()
    r.height = 52
    const c = r.addText(label)
    c.titleColor = new Color("#faf9f5")
    if (status.connected) {
      r.onSelect = async () => {
        await send({ anim: animIdx })
        Script.complete()
        App.close()
      }
    } else {
      c.subtitleColor = new Color("#555")
    }
    table.addRow(r)
  }

  // ── Auto mode ──
  sectionRow("AUTO")
  animRow("↺  Auto — follow usage rate", -1)

  // ── Idle ──
  sectionRow("IDLE")
  for (const i of [0, 1, 2]) animRow(ANIM_NAMES[i], i)

  // ── Expression ──
  sectionRow("EXPRESSION")
  for (const i of [3, 4, 5]) animRow(ANIM_NAMES[i], i)

  // ── Dance ──
  sectionRow("DANCE")
  for (const i of [6, 7, 10, 11, 12]) animRow(ANIM_NAMES[i], i)

  // ── Work ──
  sectionRow("WORK")
  for (const i of [8, 9]) animRow(ANIM_NAMES[i], i)

  // ── Screen mode ──
  sectionRow("SCREEN")
  for (const [label, mode] of [["🎨  Sprite", "sprite"], ["📊  Usage", "usage"]]) {
    const r = new UITableRow()
    r.height = 52
    const c = r.addText(label)
    c.titleColor = new Color("#faf9f5")
    if (status.connected) {
      r.onSelect = async () => {
        await send({ mode })
        Script.complete()
        App.close()
      }
    }
    table.addRow(r)
  }

  await table.present(false)
}

// ── Entry point ───────────────────────────────────────────────

if (config.runsInWidget) {
  const w = await buildWidget()
  Script.setWidget(w)
  Script.complete()
} else {
  await showUI()
}
