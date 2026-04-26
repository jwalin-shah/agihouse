/// <reference types="vite/client" />
import {
  ImuReportPace,
  OsEventTypeList,
  waitForEvenAppBridge,
} from '@evenrealities/even_hub_sdk'
import { setBridge, initHUD, showHUD, clearHUD } from './hud'

// ── Config ────────────────────────────────────────────────────────────────────

// Set VITE_INBOX_WS_URL only when using a tunnel or non-standard backend host.
// By default, connect back to the same host that served the WebView app.
function defaultWsUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.hostname || 'localhost'
  return `${protocol}//${host}:9849/g2/ws`
}

const configuredWsUrl = (import.meta.env.VITE_INBOX_WS_URL ?? '').trim()
const WS_URL = configuredWsUrl || defaultWsUrl()

// ── State ─────────────────────────────────────────────────────────────────────

let socket: WebSocket | null = null
let lastLine1 = ''
let lastLine2 = ''
let browserPreview = false
let previewStatusEl: HTMLElement | null = null
let previewLine1El: HTMLElement | null = null
let previewLine2El: HTMLElement | null = null

// IMU attention gate — rolling variance over last 10 samples
const imuWindow: number[] = []
const IMU_WINDOW_SIZE = 10
const FOCUSED_VARIANCE_THRESHOLD = 0.02

// ── State persistence (localStorage) ──────────────────────────────────────────

const HUD_STATE_KEY = 'g2.hudState'

function loadHudState() {
  try {
    const raw = localStorage.getItem(HUD_STATE_KEY)
    if (!raw) return
    const s = JSON.parse(raw) as { lastLine1?: string; lastLine2?: string }
    lastLine1 = s.lastLine1 ?? ''
    lastLine2 = s.lastLine2 ?? ''
  } catch {
    // ignore
  }
}

function persistHudState() {
  try {
    localStorage.setItem(HUD_STATE_KEY, JSON.stringify({ lastLine1, lastLine2 }))
  } catch {
    // ignore
  }
}

// ── Browser preview ──────────────────────────────────────────────────────────

function renderBrowserPreview() {
  browserPreview = true
  document.body.innerHTML = `
    <main class="preview-shell">
      <section class="preview-meta">
        <span>Ambient Copilot</span>
        <span id="preview-status">connecting</span>
      </section>
      <section class="g2-screen" aria-label="G2 HUD preview">
        <div id="preview-line1" class="line1">${lastLine1 || 'Waiting for signal'}</div>
        <div id="preview-line2" class="line2">${lastLine2 || 'Trigger one from demo panel'}</div>
      </section>
    </main>
  `
  const style = document.createElement('style')
  style.textContent = `
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #050505;
      color: #3CFA44;
      font-family: "SF Mono", "Fira Code", ui-monospace, monospace;
    }
    .preview-shell {
      width: min(92vw, 680px);
      display: grid;
      gap: 12px;
    }
    .preview-meta {
      display: flex;
      justify-content: space-between;
      color: #7b7b7b;
      font-size: 12px;
      letter-spacing: 0;
    }
    #preview-status { color: #3CFA44; }
    .g2-screen {
      aspect-ratio: 2 / 1;
      width: 100%;
      max-height: 288px;
      border: 2px solid #3CFA44;
      background: #000;
      display: grid;
      align-content: start;
      padding: 48px 24px;
      overflow: hidden;
    }
    .line1 {
      min-height: 72px;
      font-size: clamp(22px, 5vw, 34px);
      line-height: 1.15;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .line2 {
      margin-top: 18px;
      min-height: 48px;
      color: rgba(60, 250, 68, 0.72);
      font-size: clamp(17px, 3.5vw, 24px);
      line-height: 1.2;
      overflow-wrap: anywhere;
    }
  `
  document.head.appendChild(style)
  previewStatusEl = document.getElementById('preview-status')
  previewLine1El = document.getElementById('preview-line1')
  previewLine2El = document.getElementById('preview-line2')
}

function setPreviewStatus(status: string) {
  if (previewStatusEl) previewStatusEl.textContent = status
}

function updatePreview(line1: string, line2: string) {
  if (!browserPreview) return
  if (previewLine1El) previewLine1El.textContent = line1 || 'Waiting for signal'
  if (previewLine2El) previewLine2El.textContent = line2 || 'Trigger one from demo panel'
}

function renderHUD(line1: string, line2: string) {
  if (browserPreview) {
    updatePreview(line1, line2)
    return
  }
  showHUD(line1, line2)
}

function renderClear() {
  if (browserPreview) {
    updatePreview('', '')
    return
  }
  clearHUD()
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectWS() {
  if (socket?.readyState === WebSocket.OPEN) return

  socket = new WebSocket(WS_URL)

  socket.onopen = () => {
    console.log('[g2] WebSocket connected')
    setPreviewStatus('connected')
    // Replay last known state on reconnect
    if (lastLine1) renderHUD(lastLine1, lastLine2)
  }

  socket.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'hud') {
        lastLine1 = msg.line1 ?? ''
        lastLine2 = msg.line2 ?? ''
        persistHudState()
        renderHUD(lastLine1, lastLine2)
      } else if (msg.type === 'clear') {
        lastLine1 = ''
        lastLine2 = ''
        persistHudState()
        renderClear()
      }
    } catch {
      // ignore malformed
    }
  }

  socket.onclose = () => {
    console.log('[g2] WebSocket closed — reconnecting in 3s')
    setPreviewStatus('reconnecting')
    setTimeout(connectWS, 3000)
  }

  socket.onerror = () => {
    socket?.close()
  }
}

function sendIMUState(attentionState: 'focused' | 'ambient') {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'imu', attentionState }))
  }
}

// ── IMU attention gate ────────────────────────────────────────────────────────

function computeVariance(vals: number[]): number {
  if (vals.length < 2) return 1
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length
  return vals.reduce((acc, v) => acc + (v - mean) ** 2, 0) / vals.length
}

function onImuData(x: number, y: number, z: number) {
  const magnitude = Math.sqrt(x * x + y * y + z * z)
  imuWindow.push(magnitude)
  if (imuWindow.length > IMU_WINDOW_SIZE) imuWindow.shift()

  const variance = computeVariance(imuWindow)
  const state = variance < FOCUSED_VARIANCE_THRESHOLD ? 'focused' : 'ambient'
  sendIMUState(state)
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

function waitForBridge(timeoutMs: number) {
  return Promise.race([
    waitForEvenAppBridge(),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
  ])
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, fallback: T): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((resolve) => setTimeout(() => resolve(fallback), timeoutMs)),
  ])
}

async function main() {
  loadHudState()

  const bridge = await waitForBridge(3500)
  if (!bridge) {
    renderBrowserPreview()
    connectWS()
    return
  }

  setBridge(bridge)

  const ok = await withTimeout(initHUD(), 2000, false)
  if (!ok) {
    console.warn('[g2] HUD init unavailable; starting browser preview')
    renderBrowserPreview()
    connectWS()
    return
  }

  // Connect to agent server
  connectWS()

  // Start IMU for attention gate
  try {
    await bridge.imuControl(true, ImuReportPace.P500)
  } catch (err) {
    console.warn('[g2] IMU unavailable; continuing without attention gate', err)
  }

  // Subscribe to all events
  const unsubscribe = bridge.onEvenHubEvent((event) => {
    // IMU data → attention state
    if (event.sysEvent?.imuData) {
      const { x = 0, y = 0, z = 0 } = event.sysEvent.imuData
      onImuData(x, y, z)
    }

    // Double-tap → exit
    if (event.sysEvent?.eventType === OsEventTypeList.DOUBLE_CLICK_EVENT) {
      bridge.shutDownPageContainer(1)
    }

    // Foreground restored → reconnect WebSocket and replay last HUD
    if (event.sysEvent?.eventType === OsEventTypeList.FOREGROUND_ENTER_EVENT) {
      loadHudState()
      connectWS()
      if (lastLine1) renderHUD(lastLine1, lastLine2)
    }
  })

  // Wearing detection → notify server when glasses go on/off
  bridge.onDeviceStatusChanged((status) => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'wearing', isWearing: status.isWearing ?? false }))
    }
  })

  // Cleanup
  window.addEventListener('beforeunload', () => {
    bridge.imuControl(false)
    bridge.audioControl(false)
    unsubscribe()
  })
}

main()
