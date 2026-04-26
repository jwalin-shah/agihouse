import {
  waitForEvenAppBridge,
  TextContainerProperty,
  CreateStartUpPageContainer,
  TextContainerUpgrade,
  OsEventTypeList,
} from '@evenrealities/even_hub_sdk'

const BRIDGE_URL = (import.meta.env.VITE_BRIDGE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

// Browser-visible debug overlay. Renders pushes even when the Even Hub SDK
// bridge isn't available (i.e. when this page is opened in a regular browser
// rather than through the Even Hub companion). Lets us verify the SSE pipe
// without the glasses.
{
  const overlay = document.createElement('div')
  overlay.style.cssText =
    'position:fixed;inset:0;background:#000;color:#fff;font:24px/1.4 system-ui,sans-serif;padding:32px;white-space:pre-wrap;'
  overlay.textContent = 'Listening for nudges…'
  document.body.appendChild(overlay)
  const debugES = new EventSource(`${BRIDGE_URL}/events`)
  debugES.onopen = () => {
    overlay.style.borderLeft = '6px solid #4ade80'
  }
  debugES.onmessage = ev => {
    try {
      const { text } = JSON.parse(ev.data) as { text?: string }
      if (text) overlay.textContent = text
    } catch {}
  }
  debugES.onerror = () => {
    overlay.style.borderLeft = '6px solid #f87171'
  }
}

const bridge = await waitForEvenAppBridge()

const main = new TextContainerProperty({
  xPosition: 0,
  yPosition: 0,
  width: 576,
  height: 288,
  borderWidth: 0,
  borderColor: 5,
  paddingLength: 4,
  containerID: 1,
  containerName: 'whisper',
  content: 'Listening for nudges…',
  isEventCapture: 1,
})

const created = await bridge.createStartUpPageContainer(
  new CreateStartUpPageContainer({ containerTotalNum: 1, textObject: [main] }),
)
if (created !== 0) console.error('createStartUpPageContainer failed:', created)

let lastRender = ''
let pending = ''
let renderTimer: number | null = null

function render(text: string) {
  pending = text.slice(-240)
  if (renderTimer !== null) return
  renderTimer = window.setTimeout(async () => {
    renderTimer = null
    if (pending === lastRender) return
    lastRender = pending
    await bridge.textContainerUpgrade(
      new TextContainerUpgrade({
        containerID: 1,
        containerName: 'whisper',
        content: pending,
      }),
    )
  }, 120)
}

let es: EventSource | null = null
let reconnectDelay = 1000

function connect() {
  es = new EventSource(`${BRIDGE_URL}/events`)
  es.onopen = () => {
    reconnectDelay = 1000
  }
  es.onmessage = ev => {
    try {
      const { text } = JSON.parse(ev.data) as { text?: string }
      if (text) render(text)
    } catch (err) {
      console.error('bad event payload:', err)
    }
  }
  es.onerror = () => {
    es?.close()
    es = null
    window.setTimeout(connect, reconnectDelay)
    reconnectDelay = Math.min(reconnectDelay * 2, 15000)
  }
}
connect()

// Audio capture: G2 mic streams 16kHz s16le mono PCM in ~100ms frames.
// Buffer ~1s and POST to /audio so the laptop can run Whisper + recall.
const AUDIO_FLUSH_BYTES = 32_000  // ~1s of 16kHz s16le mono = 16000 samples * 2 bytes
const audioBuffer: Uint8Array[] = []
let audioBufferedBytes = 0

async function flushAudio() {
  if (audioBufferedBytes === 0) return
  const out = new Uint8Array(audioBufferedBytes)
  let off = 0
  for (const chunk of audioBuffer) {
    out.set(chunk, off)
    off += chunk.byteLength
  }
  audioBuffer.length = 0
  audioBufferedBytes = 0
  try {
    await fetch(`${BRIDGE_URL}/audio`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: out,
    })
  } catch (err) {
    console.error('audio POST failed:', err)
  }
}

const unsubscribe = bridge.onEvenHubEvent(event => {
  if (event.audioEvent?.audioPcm) {
    const pcm = event.audioEvent.audioPcm
    audioBuffer.push(pcm)
    audioBufferedBytes += pcm.byteLength
    if (audioBufferedBytes >= AUDIO_FLUSH_BYTES) flushAudio()
    return
  }

  const sysType = event.sysEvent?.eventType ?? null
  const textType = event.textEvent?.eventType ?? null

  if (sysType === OsEventTypeList.DOUBLE_CLICK_EVENT || textType === OsEventTypeList.DOUBLE_CLICK_EVENT) {
    bridge.audioControl(false)
    bridge.shutDownPageContainer(1)
    return
  }

  if (sysType === OsEventTypeList.SYSTEM_EXIT_EVENT || sysType === OsEventTypeList.ABNORMAL_EXIT_EVENT) {
    bridge.audioControl(false)
    es?.close()
    unsubscribe()
  }
})

// Kick off the mic. Must happen AFTER createStartUpPageContainer succeeded.
bridge.audioControl(true).catch(err => console.error('audioControl failed:', err))

window.addEventListener('beforeunload', () => {
  bridge.audioControl(false).catch(() => {})
  es?.close()
})
