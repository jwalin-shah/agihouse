import {
  waitForEvenAppBridge,
  TextContainerProperty,
  CreateStartUpPageContainer,
  TextContainerUpgrade,
  OsEventTypeList,
} from '@evenrealities/even_hub_sdk'

const BRIDGE_URL = (import.meta.env.VITE_BRIDGE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

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
  content: BRIDGE_URL ? 'Listening for nudges…' : 'VITE_BRIDGE_URL not set',
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
  if (!BRIDGE_URL) return
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

const unsubscribe = bridge.onEvenHubEvent(event => {
  const sysType = event.sysEvent?.eventType ?? null
  const textType = event.textEvent?.eventType ?? null

  if (sysType === OsEventTypeList.DOUBLE_CLICK_EVENT || textType === OsEventTypeList.DOUBLE_CLICK_EVENT) {
    bridge.shutDownPageContainer(1)
    return
  }

  if (sysType === OsEventTypeList.SYSTEM_EXIT_EVENT || sysType === OsEventTypeList.ABNORMAL_EXIT_EVENT) {
    es?.close()
    unsubscribe()
  }
})

window.addEventListener('beforeunload', () => {
  es?.close()
})
