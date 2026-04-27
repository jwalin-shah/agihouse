import {
  waitForEvenAppBridge,
  TextContainerProperty,
  CreateStartUpPageContainer,
  TextContainerUpgrade,
  OsEventTypeList,
} from '@evenrealities/even_hub_sdk'

const BRIDGE_URL = (import.meta.env.VITE_BRIDGE_URL as string | undefined)?.replace(/\/$/, '') ?? ''
const API_KEY = (import.meta.env.VITE_AGIHOUSE_API_KEY as string | undefined)?.trim() ?? ''
const MAX_HISTORY = 40
const HUD_MAX_CHARS = 240

type HudKind = 'ready' | 'recall' | 'proposal' | 'executed' | 'rejected' | 'suppressed' | 'note'

type BridgeEvent = {
  text?: string
  kind?: HudKind
  title?: string
  body?: string
  meta?: string
  confidence?: number
  card?: {
    title?: string
    preview?: string
    confidence?: number | null
    action?: string
  }
}

type HudMessage = {
  raw: string
  kind: HudKind
  title: string
  body: string
  meta: string
}

function clean(input: string | undefined, fallback = ''): string {
  return (input ?? fallback).replace(/\s+/g, ' ').trim()
}

function inferKind(text: string): HudKind {
  const lower = text.toLowerCase()
  if (lower.startsWith('proposed[')) return 'proposal'
  if (lower.startsWith('executed[')) return 'executed'
  if (lower.startsWith('rejected[')) return 'rejected'
  if (lower.startsWith('noted:')) return 'note'
  if (lower.includes('suppressed') || lower.includes('blocked')) return 'suppressed'
  if (lower === 'listening for nudges...' || lower === 'listening for nudges…') return 'ready'
  return text.includes(' — ') ? 'recall' : 'note'
}

function splitTitleBody(text: string, kind: HudKind): { title: string; body: string; meta: string } {
  if (kind === 'ready') return { title: 'AGIHOUSE', body: 'Listening for nudges', meta: 'audio + context live' }

  const proposal = text.match(/^(Proposed|Executed|Rejected)\[([^\]]+)\]\s*:?\s*(.*)$/i)
  if (proposal) {
    return {
      title: proposal[1].toUpperCase(),
      body: clean(proposal[3], text),
      meta: `id ${proposal[2]}`,
    }
  }

  const [left, ...rest] = text.split(' — ')
  if (rest.length > 0) {
    return { title: clean(left), body: clean(rest.join(' — ')), meta: kind.toUpperCase() }
  }

  const colon = text.indexOf(':')
  if (colon > 0 && colon < 32) {
    return {
      title: clean(text.slice(0, colon)).toUpperCase(),
      body: clean(text.slice(colon + 1)),
      meta: kind.toUpperCase(),
    }
  }

  return { title: kind.toUpperCase(), body: text, meta: 'live' }
}

function normalizeEvent(payload: BridgeEvent): HudMessage | null {
  const card = payload.card
  const text = clean(payload.text ?? card?.preview ?? payload.body)
  const kind = payload.kind ?? inferKind(text)
  const parts = splitTitleBody(text, kind)
  const confidence = payload.confidence ?? card?.confidence
  const confidenceText =
    typeof confidence === 'number' ? `CONF ${Math.round(confidence * 100)}%` : ''
  return {
    raw: text,
    kind,
    title: clean(payload.title ?? card?.title ?? parts.title, parts.title),
    body: clean(payload.body ?? card?.preview ?? parts.body, parts.body),
    meta: clean(payload.meta ?? (confidenceText || parts.meta), parts.meta),
  }
}

function clampLine(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

function formatForHud(message: HudMessage): string {
  const label = message.kind.toUpperCase()
  const lines = [
    `${label}  ${message.meta}`.trim(),
    clampLine(message.title, 34),
    clampLine(message.body, 72),
  ].filter(Boolean)
  return lines.join('\n').slice(0, HUD_MAX_CHARS)
}

function formatForHudContainers(message: HudMessage): { header: string; title: string; body: string } {
  const label = message.kind.toUpperCase()
  return {
    header: `${label}  ${message.meta}`.trim().slice(0, 46),
    title: clampLine(message.title, 38),
    body: clampLine(message.body, 125),
  }
}

// Browser-visible debug overlay. Renders pushes even when the Even Hub SDK
// bridge isn't available (i.e. when this page is opened in a regular browser
// rather than through the Even Hub companion). Lets us verify the SSE pipe
// without the glasses.
{
  const overlay = document.createElement('div')
  overlay.style.cssText = [
    'position:fixed',
    'inset:0',
    'background:#050505',
    'color:#f8fafc',
    'font:20px/1.35 Inter,ui-sans-serif,system-ui,sans-serif',
    'padding:28px',
    'display:grid',
    'grid-template-rows:auto auto 1fr',
    'gap:18px',
    'letter-spacing:0',
  ].join(';')
  document.body.appendChild(overlay)

  const stage = document.createElement('main')
  stage.style.cssText = [
    'display:grid',
    'grid-template-columns:minmax(320px,576px) minmax(260px,360px)',
    'gap:18px',
    'align-items:start',
    'width:min(960px,calc(100vw - 56px))',
  ].join(';')
  overlay.append(stage)

  const frame = document.createElement('section')
  frame.style.cssText = [
    'width:100%',
    'height:min(288px,calc(100vh - 180px))',
    'min-height:220px',
    'border:1px solid #334155',
    'border-radius:8px',
    'padding:18px 20px',
    'box-sizing:border-box',
    'background:#0b0f14',
    'box-shadow:0 18px 60px rgba(0,0,0,.45)',
    'display:grid',
    'grid-template-rows:auto 1fr auto',
    'gap:14px',
  ].join(';')

  const status = document.createElement('div')
  status.style.cssText =
    'font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#94a3b8;display:flex;justify-content:space-between;gap:16px;'

  const title = document.createElement('div')
  title.style.cssText = 'font-weight:750;font-size:30px;line-height:1.05;color:#fff;overflow:hidden;text-overflow:ellipsis;'

  const body = document.createElement('div')
  body.style.cssText = 'font-size:22px;line-height:1.25;color:#dbeafe;overflow:hidden;'

  const footer = document.createElement('div')
  footer.style.cssText = 'font-size:13px;color:#64748b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'

  frame.append(status, title, body, footer)
  stage.append(frame)

  const aside = document.createElement('aside')
  aside.style.cssText = [
    'display:grid',
    'gap:10px',
    'font-size:13px',
    'min-width:0',
  ].join(';')
  stage.append(aside)

  function panel(label: string): HTMLDivElement {
    const node = document.createElement('div')
    node.style.cssText = [
      'border:1px solid #1f2937',
      'border-radius:8px',
      'background:#080b10',
      'padding:12px',
      'min-height:54px',
      'box-sizing:border-box',
    ].join(';')
    const head = document.createElement('div')
    head.textContent = label
    head.style.cssText = 'font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:8px;'
    const content = document.createElement('div')
    content.style.cssText = 'display:grid;gap:6px;color:#cbd5e1;'
    node.append(head, content)
    aside.append(node)
    return content
  }

  const proposalPanel = panel('Pending proposals')
  const schedulePanel = panel('Scheduled sends')
  const memoryPanel = panel('Learned memory')
  const auditPanel = panel('Audit')
  const controlsPanel = panel('Controls')

  const recent = document.createElement('div')
  recent.style.cssText =
    'width:min(960px,calc(100vw - 56px));display:grid;gap:8px;color:#94a3b8;font-size:14px;'
  overlay.append(recent)

  const row = (text: string, tone = '#cbd5e1') => {
    const node = document.createElement('div')
    node.style.cssText = `overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:${tone};`
    node.textContent = text
    return node
  }

  function requestHeaders(): HeadersInit {
    return API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {}
  }

  async function fetchJson(path: string): Promise<unknown | null> {
    try {
      const res = await fetch(`${BRIDGE_URL}${path}`, { headers: requestHeaders() })
      if (!res.ok) return null
      return await res.json()
    } catch {
      return null
    }
  }

  async function postJson(path: string, body?: unknown): Promise<unknown | null> {
    try {
      const res = await fetch(`${BRIDGE_URL}${path}`, {
        method: 'POST',
        headers: {
          ...requestHeaders(),
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!res.ok) return null
      return await res.json()
    } catch {
      return null
    }
  }

  function button(label: string, onClick: () => void, tone = '#1f2937'): HTMLButtonElement {
    const node = document.createElement('button')
    node.type = 'button'
    node.textContent = label
    node.style.cssText = [
      'appearance:none',
      'border:1px solid #334155',
      `background:${tone}`,
      'color:#f8fafc',
      'border-radius:6px',
      'padding:7px 9px',
      'font:12px/1.1 ui-sans-serif,system-ui,sans-serif',
      'cursor:pointer',
      'letter-spacing:0',
    ].join(';')
    node.onclick = onClick
    return node
  }

  function proposalNode(p: { id?: string; action?: string; card?: { title?: string; preview?: string } }): HTMLDivElement {
    const node = document.createElement('div')
    node.style.cssText = 'display:grid;gap:8px;border-top:1px solid #111827;padding-top:8px;'
    const text = row(`${p.action ?? 'action'} · ${p.card?.title ?? p.card?.preview ?? 'pending'}`)
    const actions = document.createElement('div')
    actions.style.cssText = 'display:flex;gap:6px;'
    if (p.id) {
      actions.append(
        button('Confirm', async () => {
          await postJson(`/proposals/${p.id}/confirm`)
          await refreshPanels()
        }, '#14532d'),
        button('Reject', async () => {
          await postJson(`/proposals/${p.id}/reject`, { reason: 'simulator_rejected' })
          await refreshPanels()
        }, '#5f1d1d'),
      )
    }
    node.append(text, actions)
    return node
  }

  async function refreshPanels() {
    const proposals = await fetchJson('/proposals') as { proposals?: Array<{ id?: string; action?: string; card?: { title?: string; preview?: string } }> } | null
    const scheduled = await fetchJson('/scheduled-imessages') as { scheduled?: Array<{ handle?: string; text?: string; status?: string; send_at?: number }> } | null
    const memory = await fetchJson('/memory/edges') as { edges?: Array<{ subject?: string; relation?: string; object?: string; confidence?: number }> } | null
    const audit = await fetchJson('/audit/summary') as { summary?: { total?: number; by_decision?: Record<string, number> } } | null

    const proposed = (proposals?.proposals ?? []).filter(p => p)
    proposalPanel.replaceChildren(
      ...(proposed.length ? proposed.slice(0, 3).map(p => proposalNode(p)) : [row('none', '#64748b')]),
    )

    const jobs = scheduled?.scheduled ?? []
    schedulePanel.replaceChildren(
      ...(jobs.length ? jobs.slice(0, 4).map(j => {
        const when = j.send_at ? new Date(j.send_at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '?'
        return row(`${j.status ?? 'scheduled'} ${when} · ${j.handle ?? ''} · ${j.text ?? ''}`)
      }) : [row('none', '#64748b')]),
    )

    const edges = memory?.edges ?? []
    memoryPanel.replaceChildren(
      ...(edges.length ? edges.slice(0, 4).map(edge => {
        const conf = typeof edge.confidence === 'number' ? `${Math.round(edge.confidence * 100)}%` : '?'
        return row(`${conf} · ${edge.subject ?? '?'} ${edge.relation ?? 'relates_to'} ${edge.object ?? '?'}`)
      }) : [row('none yet', '#64748b')]),
    )

    const counts = audit?.summary?.by_decision ?? {}
    auditPanel.replaceChildren(
      row(`total ${audit?.summary?.total ?? 0}`),
      row(`fired ${counts.fired ?? 0} · proposed ${counts.proposed ?? 0} · suppressed ${counts.suppressed ?? 0}`),
    )
  }

  controlsPanel.replaceChildren(
    button('Daniel', async () => {
      await postJson('/demo/who_is_daniel')
      await refreshPanels()
    }),
    button('Departure', async () => {
      await postJson('/demo/departure')
      await refreshPanels()
    }),
    button('Silence', async () => {
      await postJson('/demo/silence')
      await refreshPanels()
    }),
    button('Run due sends', async () => {
      await postJson('/scheduled-imessages/run-due')
      await refreshPanels()
    }),
  )
  refreshPanels()
  window.setInterval(refreshPanels, 3000)

  const debugHistory: HudMessage[] = []
  const renderDebug = (message: HudMessage, connected: boolean) => {
    status.innerHTML = `<span>${message.kind}</span><span>${connected ? 'SSE live' : 'reconnecting'}</span>`
    title.textContent = message.title
    body.textContent = message.body
    footer.textContent = message.meta
    frame.style.borderColor =
      message.kind === 'proposal' ? '#facc15' :
      message.kind === 'executed' ? '#22c55e' :
      message.kind === 'suppressed' || message.kind === 'rejected' ? '#f87171' :
      '#38bdf8'

    debugHistory.push(message)
    while (debugHistory.length > 3) debugHistory.shift()
    recent.replaceChildren(
      ...debugHistory.slice().reverse().map(item => {
        const row = document.createElement('div')
        row.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'
        row.textContent = `${item.kind.toUpperCase()} · ${item.title} · ${item.body}`
        return row
      }),
    )
  }

  renderDebug(normalizeEvent({ text: 'Listening for nudges…' })!, false)
  const debugES = new EventSource(`${BRIDGE_URL}/events`)
  debugES.onopen = () => {
    const current = debugHistory[debugHistory.length - 1] ?? normalizeEvent({ text: 'Listening for nudges…' })!
    renderDebug(current, true)
  }
  debugES.onmessage = ev => {
    try {
      const message = normalizeEvent(JSON.parse(ev.data) as BridgeEvent)
      if (message) renderDebug(message, true)
    } catch {}
  }
  debugES.onerror = () => {
    const current = debugHistory[debugHistory.length - 1] ?? normalizeEvent({ text: 'Listening for nudges…' })!
    renderDebug(current, false)
  }
}

const bridge = await waitForEvenAppBridge()

const headerContainer = new TextContainerProperty({
  xPosition: 0,
  yPosition: 0,
  width: 576,
  height: 34,
  borderWidth: 0,
  borderColor: 5,
  paddingLength: 2,
  containerID: 1,
  containerName: 'hud_header',
  content: 'READY  audio + context live',
  isEventCapture: 1,
})

const titleContainer = new TextContainerProperty({
  xPosition: 0,
  yPosition: 40,
  width: 576,
  height: 72,
  borderWidth: 0,
  borderColor: 5,
  paddingLength: 2,
  containerID: 2,
  containerName: 'hud_title',
  content: 'AGIHOUSE',
  isEventCapture: 1,
})

const bodyContainer = new TextContainerProperty({
  xPosition: 0,
  yPosition: 120,
  width: 576,
  height: 168,
  borderWidth: 0,
  borderColor: 5,
  paddingLength: 2,
  containerID: 3,
  containerName: 'hud_body',
  content: 'Listening for nudges',
  isEventCapture: 1,
})

const created = await bridge.createStartUpPageContainer(
  new CreateStartUpPageContainer({
    containerTotalNum: 3,
    textObject: [headerContainer, titleContainer, bodyContainer],
  }),
)
if (created !== 0) console.error('createStartUpPageContainer failed:', created)

let lastRender = ''
let pending: HudMessage | null = null
let renderTimer: number | null = null
const history: HudMessage[] = [normalizeEvent({ text: 'Listening for nudges…' })!]
let historyIndex = 0

function currentMessage(): HudMessage {
  return history[historyIndex] ?? history[0]
}

function pushHistory(message: HudMessage) {
  const last = history[history.length - 1]
  if (message.raw === last?.raw) return
  history.push(message)
  if (history.length > MAX_HISTORY) history.shift()
  historyIndex = history.length - 1
}

function stepHistory(delta: number) {
  const next = Math.max(0, Math.min(history.length - 1, historyIndex + delta))
  if (next === historyIndex) return
  historyIndex = next
  render(currentMessage())
}

function render(message: HudMessage) {
  pending = message
  if (renderTimer !== null) return
  renderTimer = window.setTimeout(async () => {
    renderTimer = null
    if (!pending) return
    const formatted = formatForHud(pending)
    if (formatted === lastRender) return
    lastRender = formatted
    const parts = formatForHudContainers(pending)
    await Promise.all([
      bridge.textContainerUpgrade(
        new TextContainerUpgrade({
          containerID: 1,
          containerName: 'hud_header',
          content: parts.header,
        }),
      ),
      bridge.textContainerUpgrade(
        new TextContainerUpgrade({
          containerID: 2,
          containerName: 'hud_title',
          content: parts.title,
        }),
      ),
      bridge.textContainerUpgrade(
        new TextContainerUpgrade({
          containerID: 3,
          containerName: 'hud_body',
          content: parts.body,
        }),
      ),
    ])
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
      const message = normalizeEvent(JSON.parse(ev.data) as BridgeEvent)
      if (message) {
        pushHistory(message)
        render(currentMessage())
      }
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
let audioInFlight = false

async function flushAudio() {
  if (audioBufferedBytes === 0 || audioInFlight) return
  audioInFlight = true
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
  } finally {
    audioInFlight = false
  }
}

const audioFlushTimer = window.setInterval(() => {
  flushAudio()
}, 1200)

function isEventTypeMatch(
  actual: number | null | undefined,
  enumKey: string,
): boolean {
  const target = (OsEventTypeList as unknown as Record<string, number | undefined>)[enumKey]
  return target !== undefined && actual === target
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

  // Scroll history back with single click, forward with long press when available.
  if (isEventTypeMatch(sysType, 'SINGLE_CLICK_EVENT') || isEventTypeMatch(textType, 'SINGLE_CLICK_EVENT')) {
    stepHistory(-1)
    return
  }
  if (isEventTypeMatch(sysType, 'LONG_PRESS_EVENT') || isEventTypeMatch(textType, 'LONG_PRESS_EVENT')) {
    stepHistory(1)
    return
  }

  if (sysType === OsEventTypeList.DOUBLE_CLICK_EVENT || textType === OsEventTypeList.DOUBLE_CLICK_EVENT) {
    bridge.audioControl(false)
    bridge.shutDownPageContainer(1)
    bridge.shutDownPageContainer(2)
    bridge.shutDownPageContainer(3)
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
  window.clearInterval(audioFlushTimer)
  bridge.audioControl(false).catch(() => {})
  es?.close()
})
