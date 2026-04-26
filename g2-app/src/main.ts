/// <reference types="vite/client" />
import {
  ImuReportPace,
  OsEventTypeList,
  waitForEvenAppBridge,
} from '@evenrealities/even_hub_sdk'
import { setBridge, initHUD, showHUD, clearHUD } from './hud'

// ── Config ────────────────────────────────────────────────────────────────────

// Set VITE_INBOX_WS_URL in .env.local to your laptop's ngrok/IP
// e.g. VITE_INBOX_WS_URL=ws://192.168.1.50:9849/g2/ws
const WS_URL = import.meta.env.VITE_INBOX_WS_URL ?? 'ws://localhost:9849/g2/ws'

// ── State ─────────────────────────────────────────────────────────────────────

let socket: WebSocket | null = null
let lastLine1 = ''
let lastLine2 = ''

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

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectWS() {
  if (socket?.readyState === WebSocket.OPEN) return

  socket = new WebSocket(WS_URL)

  socket.onopen = () => {
    console.log('[g2] WebSocket connected')
    // Replay last known state on reconnect
    if (lastLine1) showHUD(lastLine1, lastLine2)
  }

  socket.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'hud') {
        lastLine1 = msg.line1 ?? ''
        lastLine2 = msg.line2 ?? ''
        persistHudState()
        showHUD(lastLine1, lastLine2)
      } else if (msg.type === 'clear') {
        lastLine1 = ''
        lastLine2 = ''
        persistHudState()
        clearHUD()
      }
    } catch {
      // ignore malformed
    }
  }

  socket.onclose = () => {
    console.log('[g2] WebSocket closed — reconnecting in 3s')
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

async function main() {
  loadHudState()

  const bridge = await waitForEvenAppBridge()
  setBridge(bridge)

  const ok = await initHUD()
  if (!ok) {
    console.error('[g2] HUD init failed')
    return
  }

  // Connect to agent server
  connectWS()

  // Start IMU for attention gate
  await bridge.imuControl(true, ImuReportPace.P500)

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
      if (lastLine1) showHUD(lastLine1, lastLine2)
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
