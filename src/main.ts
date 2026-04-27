import './styles.css'

type AgentState = 'sleeping' | 'listening' | 'thinking' | 'noticing' | 'answering' | 'locked'
type AppMode = 'live' | 'ask'

type MemoryEntry = {
  id: string
  timestamp: string
  date: string
  hour: string
  minute: string
  summary: string
  people: string[]
  topics: string[]
  decisions: string[]
  tasks: string[]
  promises: string[]
  hud: string
}

type EvenSdk = Record<string, any>
type EvenBridge = Record<string, any>

const backendUrl =
  (import.meta.env.VITE_PRESENCE_BACKEND_URL as string | undefined) ||
  (import.meta.env.VITE_PUBLIC_BACKEND_URL as string | undefined) ||
  `${window.location.protocol}//${window.location.hostname}:8787`
const g2SampleRate = 16000
const g2BytesPerSecond = g2SampleRate * 2
const g2AudioChunkSeconds = Math.max(10, Number(import.meta.env.VITE_G2_AUDIO_CHUNK_SECONDS || 60))
const g2AskChunkSeconds = Math.max(4, Number(import.meta.env.VITE_G2_ASK_CHUNK_SECONDS || 8))
const g2AudioTargetBytes = g2BytesPerSecond * g2AudioChunkSeconds
const g2AskTargetBytes = g2BytesPerSecond * g2AskChunkSeconds

let state: AgentState = 'sleeping'
let appMode: AppMode = 'live'
let hud = 'LIVE ready\nTap to store\nSwipe up ask'
let bridge: EvenBridge | null = null
let sdk: EvenSdk | null = null
let recorder: MediaRecorder | null = null
let speechRecognition: any = null
let g2MicActive = false
let g2PcmChunks: Uint8Array[] = []
let g2PcmBytes = 0
let g2FlushTimer: number | undefined
let g2AudioSendQueue = Promise.resolve()
let g2HudTimer: number | undefined
let g2WindowStartedAt = 0
let lastG2HudTick = 0
let assistHoldUntil = 0
let ownerConfirmed = true
let recentEntries: MemoryEntry[] = []
let lastAvatarState: AgentState | null = null
let imageUpdateQueue = Promise.resolve()
let askProgressTimer: number | undefined

const app = document.querySelector<HTMLDivElement>('#app')
if (!app) throw new Error('Missing #app')

app.innerHTML = `
  <main class="shell">
    <section class="stage" aria-label="Presence buddy">
      <div class="buddy-wrap">
        <canvas id="phoneBuddy" class="pixel-buddy-preview" width="120" height="96"></canvas>
        <div id="thought" class="thought">LIVE ready<br />Tap to store</div>
      </div>
    </section>

    <section class="panel">
      <div class="brand">
        <h1>Presence</h1>
        <span class="pill"><span class="dot"></span><span id="connection">connecting</span></span>
      </div>

      <div id="glassPreview" class="glass-preview">LIVE ready
Tap to store
Swipe up ask</div>

      <div class="controls">
        <div class="row">
          <button id="wakeBtn" class="primary" type="button">Live mode</button>
          <button id="sleepBtn" type="button">Ask mode</button>
          <button id="micBtn" type="button">Start storing</button>
          <button id="unknownBtn" type="button">Owner voice</button>
        </div>

        <label>
          Memory snippet
          <textarea id="memoryInput" placeholder="Say or paste what happened..."></textarea>
        </label>
        <div class="row">
          <button id="rememberBtn" class="primary" type="button">Remember</button>
        </div>

        <label>
          Ask memory
          <input id="questionInput" placeholder="What did I decide to build?" />
        </label>
        <div class="row">
          <button id="askBtn" class="primary" type="button">Ask</button>
        </div>
      </div>

      <div class="memory-card">
        <h2>Recent memories</h2>
        <div id="memoryLog" class="log">
          <div class="small">No memories yet.</div>
        </div>
      </div>

      <p class="small" id="backendHint"></p>
    </section>
  </main>
`

const phoneBuddy = document.querySelector<HTMLCanvasElement>('#phoneBuddy')!
const thought = document.querySelector<HTMLDivElement>('#thought')!
const glassPreview = document.querySelector<HTMLDivElement>('#glassPreview')!
const connection = document.querySelector<HTMLSpanElement>('#connection')!
const memoryLog = document.querySelector<HTMLDivElement>('#memoryLog')!
const backendHint = document.querySelector<HTMLParagraphElement>('#backendHint')!
const memoryInput = document.querySelector<HTMLTextAreaElement>('#memoryInput')!
const questionInput = document.querySelector<HTMLInputElement>('#questionInput')!
const micBtn = document.querySelector<HTMLButtonElement>('#micBtn')!
const unknownBtn = document.querySelector<HTMLButtonElement>('#unknownBtn')!

document.querySelector<HTMLButtonElement>('#wakeBtn')!.addEventListener('click', () => {
  void switchAppMode('live')
})

document.querySelector<HTMLButtonElement>('#sleepBtn')!.addEventListener('click', () => {
  void switchAppMode('ask')
})

document.querySelector<HTMLButtonElement>('#rememberBtn')!.addEventListener('click', async () => {
  const text = memoryInput.value.trim()
  if (!text) return
  await rememberText(text)
  memoryInput.value = ''
})

document.querySelector<HTMLButtonElement>('#askBtn')!.addEventListener('click', async () => {
  const question = questionInput.value.trim()
  if (!question) return
  await askMemory(question)
})

unknownBtn.addEventListener('click', () => {
  ownerConfirmed = !ownerConfirmed
  unknownBtn.textContent = ownerConfirmed ? 'Owner voice' : 'Unknown voice'
  if (!ownerConfirmed) {
    setState('locked', 'Memory locked\nUnknown voice')
  } else {
    setState('listening', 'Owner verified\nPresence ready')
  }
})

micBtn.addEventListener('click', async () => {
  if (g2MicActive) {
    await stopG2Mic()
    return
  }
  if (recorder?.state === 'recording') {
    recorder.stop()
    micBtn.textContent = 'Start storing'
    return
  }
  if (speechRecognition) {
    stopSpeechRecognition()
    return
  }
  if (bridge) {
    await startG2Mic()
    return
  }
  if (supportsSpeechRecognition()) {
    startSpeechRecognition()
  } else {
    await startBrowserMic()
  }
})

void boot()

window.addEventListener('beforeunload', () => {
  window.clearTimeout(g2FlushTimer)
  void flushG2Audio(true)
  void bridge?.audioControl?.(false)
})

async function boot() {
  backendHint.textContent = `Backend: ${backendUrl}`
  await Promise.allSettled([connectBackend(), connectEvenBridge()])
  await refreshMemories()
  renderModeControls()
  setState(state, hud)
}

async function connectBackend() {
  try {
    const health = await api('/health')
    connection.textContent = health.hasTextKey ? 'model ready' : 'fallback mode'
    if (!health.hasSttKey && supportsSpeechRecognition()) {
      backendHint.textContent = `Backend: ${backendUrl} · Browser dictation enabled`
    }
  } catch {
    connection.textContent = 'backend offline'
  }
}

async function connectEvenBridge() {
  try {
    sdk = await import('@evenrealities/even_hub_sdk')
    registerBackgroundState()
    bridge = await withTimeout(sdk.waitForEvenAppBridge(), 1800)
    await createHudPage()
    attachEvenEvents()
  } catch {
    bridge = null
  }
}

function registerBackgroundState() {
  sdk?.setBackgroundState?.('presenceState', () => ({
    state,
    appMode,
    hud,
    g2MicActive,
    g2PcmBytes,
  }))
  sdk?.onBackgroundRestore?.('presenceState', (saved: unknown) => {
    const snapshot = saved as Partial<{
      state: AgentState
      appMode: AppMode
      hud: string
      g2MicActive: boolean
      g2PcmBytes: number
    }>
    state = snapshot.state ?? state
    appMode = snapshot.appMode ?? appMode
    hud = snapshot.hud ?? hud
    g2MicActive = snapshot.g2MicActive ?? g2MicActive
    g2PcmBytes = snapshot.g2PcmBytes ?? g2PcmBytes
    renderModeControls()
    renderPhoneBuddy()
    void updateHud(hud)
  })
}

async function createHudPage() {
  if (!bridge || !sdk) return
  const avatar = new sdk.TextContainerProperty({
    xPosition: 242,
    yPosition: 0,
    width: 92,
    height: 58,
    borderWidth: 0,
    borderColor: 5,
    borderRadius: 0,
    paddingLength: 0,
    containerID: 2,
    containerName: 'buddy',
    content: getGlassesAvatarText(state),
    isEventCapture: 0,
  })

  const text = new sdk.TextContainerProperty({
    xPosition: 0,
    yPosition: 62,
    width: 576,
    height: 226,
    borderWidth: 0,
    borderColor: 5,
    borderRadius: 0,
    paddingLength: 2,
    containerID: 1,
    containerName: 'main',
    content: hud,
    isEventCapture: 1,
  })
  await bridge.createStartUpPageContainer(
    new sdk.CreateStartUpPageContainer({
      containerTotalNum: 2,
      textObject: [avatar, text],
    }),
  )
  await updateGlassesAvatar(true)
}

function attachEvenEvents() {
  if (!bridge) return
  bridge.onEvenHubEvent?.((event: any) => {
    if (event?.audioEvent?.audioPcm) {
      handleG2Audio(event.audioEvent.audioPcm)
    }

    const textEvent = event?.textEvent
    if (textEvent) {
      const eventType = textEvent.eventType ?? 0
      if (eventType === 1) {
        void switchAppMode('ask')
        return
      }
      if (eventType === 2) {
        void switchAppMode('live')
        return
      }
    }

    const sysEvent = event?.sysEvent
    if (!sysEvent) return

    const eventType = sysEvent.eventType ?? 0
    if (eventType === 6 || eventType === 7) {
      g2MicActive = false
      window.clearTimeout(g2FlushTimer)
      void flushG2Audio(true)
      void bridge?.audioControl?.(false)
      return
    }
    if (eventType === 3) {
      bridge?.shutDownPageContainer?.(1)
      return
    }
    if (eventType === 0 && state === 'locked') {
      ownerConfirmed = true
      unknownBtn.textContent = 'Owner voice'
      setState(appMode === 'ask' ? 'answering' : 'listening', 'Owner verified\nPresence ready')
      return
    }
    if (eventType === 0) {
      if (appMode === 'live') {
        void toggleG2MicFromGlasses()
      } else {
        void askFromGlasses()
      }
    }
  })
}

async function switchAppMode(nextMode: AppMode) {
  if (appMode === nextMode && !(nextMode === 'live' && state === 'sleeping')) return
  appMode = nextMode
  renderModeControls()

  if (nextMode === 'ask') {
    if (!g2MicActive) {
      await startG2Mic('ask')
    } else {
      rescheduleG2Flush(g2AskChunkSeconds)
      setState('listening', askListeningHud(0))
    }
    return
  }

  if (g2MicActive) rescheduleG2Flush(g2AudioChunkSeconds)
  setState(g2MicActive ? 'listening' : 'sleeping', g2MicActive ? liveStoringHud(0) : 'LIVE ready\nTap to store\nSwipe up ask')
}

async function askFromGlasses() {
  const question = questionInput.value.trim()
  if (!question) {
    setState('listening', g2MicActive ? askListeningHud(0) : 'ASK mode\nListening soon\nAsk naturally')
    if (!g2MicActive) await startG2Mic('ask')
    return
  }
  await askMemory(question)
}

async function toggleG2MicFromGlasses() {
  if (state === 'locked') return
  if (g2MicActive) {
    await stopG2Mic()
  } else {
    await startG2Mic()
  }
}

async function startG2Mic(nextMode: AppMode = 'live') {
  if (!bridge) {
    setState('locked', 'G2 bridge\nnot ready')
    return
  }
  if (g2MicActive) return

  try {
    appMode = nextMode
    renderModeControls()
    const opened = await bridge.audioControl?.(true)
    if (opened === false) throw new Error('audioControl failed')
    g2MicActive = true
    g2PcmChunks = []
    g2PcmBytes = 0
    g2WindowStartedAt = Date.now()
    lastG2HudTick = 0
    micBtn.textContent = 'Stop storing'
    setState('listening', appMode === 'ask' ? askListeningHud(0) : liveStoringHud(0))
    startG2HudTicker()
    rescheduleG2Flush(appMode === 'ask' ? g2AskChunkSeconds : g2AudioChunkSeconds)
  } catch (error) {
    setState('locked', `G2 mic error\n${shortError(error)}`)
  }
}

async function stopG2Mic(showIdle = true) {
  g2MicActive = false
  window.clearTimeout(g2FlushTimer)
  window.clearInterval(g2HudTimer)
  g2FlushTimer = undefined
  g2HudTimer = undefined
  micBtn.textContent = 'Start storing'
  try {
    await flushG2Audio(true)
    await bridge?.audioControl?.(false)
  } finally {
    if (showIdle) {
      setState('sleeping', appMode === 'ask' ? 'ASK paused\nTap listens\nSwipe down live' : 'LIVE paused\nTap to store\nSwipe up ask')
    }
  }
}

function handleG2Audio(rawPcm: unknown) {
  if (!g2MicActive) return
  const pcm = toUint8Array(rawPcm)
  if (!pcm.length) return

  g2PcmChunks.push(pcm)
  g2PcmBytes += pcm.byteLength

  const seconds = g2PcmBytes / g2BytesPerSecond
  const targetBytes = appMode === 'ask' ? g2AskTargetBytes : g2AudioTargetBytes
  const targetSeconds = appMode === 'ask' ? g2AskChunkSeconds : g2AudioChunkSeconds
  const now = Date.now()
  if (now >= assistHoldUntil && (now - lastG2HudTick > 1000 || g2PcmBytes >= targetBytes)) {
    lastG2HudTick = now
    setState('listening', appMode === 'ask' ? askListeningHud(seconds) : liveStoringHud(seconds))
  }

  if (!g2FlushTimer) {
    rescheduleG2Flush(targetSeconds)
  }

  if (g2PcmBytes >= targetBytes) {
    void flushG2Audio()
  }
}

async function flushG2Audio(force = false) {
  if (!g2PcmBytes) return
  window.clearTimeout(g2FlushTimer)
  g2FlushTimer = undefined
  const chunks = g2PcmChunks
  const totalBytes = g2PcmBytes
  g2PcmChunks = []
  g2PcmBytes = 0
  g2WindowStartedAt = Date.now()

  const currentMode = appMode
  const targetBytes = currentMode === 'ask' ? g2AskTargetBytes : g2AudioTargetBytes
  const minimumBytes = force ? g2BytesPerSecond : Math.min(targetBytes, g2BytesPerSecond * (currentMode === 'ask' ? 2 : 45))
  if (totalBytes < minimumBytes) {
    if (g2MicActive) {
      setState('listening', currentMode === 'ask' ? askListeningHud(0, totalBytes) : liveStoringHud(0))
      rescheduleG2Flush(currentMode === 'ask' ? g2AskChunkSeconds : g2AudioChunkSeconds)
    }
    return
  }

  const pcm = concatUint8(chunks, totalBytes)
  g2AudioSendQueue = g2AudioSendQueue
    .catch(() => undefined)
    .then(async () => {
      setState('thinking', 'Saving memory\nTranscribing...')
      try {
        const result = await api('/api/audio/pcm', {
          method: 'POST',
          body: JSON.stringify({
            pcmBase64: uint8ToBase64(pcm),
            sampleRate: g2SampleRate,
            channels: 1,
            source: currentMode === 'ask' ? 'g2-ask' : 'g2-mic',
          }),
        })
        if (result.proactive) {
          assistHoldUntil = Date.now() + 6500
          setState(result.state || 'answering', result.proactive.hud || result.proactive.answer || 'Try this')
          await refreshMemories()
        } else if (result.text) {
          const nextHud = result.hour?.hud || result.fiveMinute?.hud || result.hud || 'Stream noted'
          setState(result.state || 'noticing', nextHud)
          await refreshMemories()
        } else if (g2MicActive) {
          setState(result.state || 'listening', result.hud || (appMode === 'ask' ? askListeningHud(0) : liveStoringHud(0)))
        }
        if (g2MicActive) {
          g2WindowStartedAt = Date.now()
          rescheduleG2Flush(appMode === 'ask' ? g2AskChunkSeconds : g2AudioChunkSeconds)
        }
      } catch (error) {
        setState('locked', `G2 STT error\n${shortError(error)}`)
      }
    })

  await g2AudioSendQueue
}

async function updateHud(nextHud: string) {
  glassPreview.textContent = nextHud
  thought.innerHTML = escapeHtml(nextHud).replace(/\n/g, '<br />')

  if (!bridge || !sdk) return

  try {
    await updateGlassesAvatar()
    if (sdk.TextContainerUpgrade) {
      await bridge.textContainerUpgrade(
        new sdk.TextContainerUpgrade({
          containerID: 1,
          containerName: 'main',
          content: nextHud,
          contentOffset: 0,
          contentLength: 0,
        }),
      )
    }
  } catch {
    // The phone preview remains authoritative if the bridge is unavailable.
  }
}

async function updateGlassesAvatar(force = false) {
  if (!bridge || !sdk) return
  if (!force && lastAvatarState === state) return
  lastAvatarState = state
  const currentSdk = sdk

  imageUpdateQueue = imageUpdateQueue
    .catch(() => undefined)
    .then(async () => {
      await bridge?.textContainerUpgrade?.(
        new currentSdk.TextContainerUpgrade({
          containerID: 2,
          containerName: 'buddy',
          content: getGlassesAvatarText(state),
          contentOffset: 0,
          contentLength: 0,
        }),
      )
    })

  await imageUpdateQueue
}

function liveStoringHud(seconds: number) {
  return `LIVE\nsaving memory\n${Math.floor(seconds)}/${g2AudioChunkSeconds}s`
}

function askListeningHud(seconds: number, bytes = g2PcmBytes) {
  if (seconds >= 3 && bytes === 0) return 'ASK\nmic warming\nspeak naturally'
  return `ASK\nlistening\n${Math.floor(seconds)}/${g2AskChunkSeconds}s`
}

function rescheduleG2Flush(seconds: number) {
  window.clearTimeout(g2FlushTimer)
  g2FlushTimer = window.setTimeout(() => {
    void flushG2Audio()
  }, seconds * 1000 + 500)
}

function startG2HudTicker() {
  window.clearInterval(g2HudTimer)
  g2HudTimer = window.setInterval(() => {
    if (!g2MicActive) return
    if (Date.now() < assistHoldUntil) return
    const elapsed = (Date.now() - g2WindowStartedAt) / 1000
    setState('listening', appMode === 'ask' ? askListeningHud(elapsed) : liveStoringHud(elapsed))
  }, 1000)
}

function renderModeControls() {
  const liveButton = document.querySelector<HTMLButtonElement>('#wakeBtn')
  const askButton = document.querySelector<HTMLButtonElement>('#sleepBtn')
  if (liveButton) liveButton.classList.toggle('primary', appMode === 'live')
  if (askButton) askButton.classList.toggle('primary', appMode === 'ask')
  micBtn.textContent = g2MicActive ? 'Stop storing' : 'Start storing'
}

function setState(nextState: AgentState, nextHud: string) {
  state = nextState
  hud = formatHud(nextHud)
  renderPhoneBuddy()
  void updateHud(hud)
}

async function rememberText(text: string) {
  setState('thinking', 'Saving memory\nObsidian...')
  try {
    const result = await api('/api/memory/text', {
      method: 'POST',
      body: JSON.stringify({
        text,
        speaker: ownerConfirmed ? 'Owner' : 'Unknown',
        source: 'typed',
      }),
    })
    setState(result.state || 'noticing', result.hud || 'Noted')
    await refreshMemories()
  } catch (error) {
    setState('locked', `Backend error\n${shortError(error)}`)
  }
}

async function askMemory(question: string) {
  if (!question.trim()) {
    setState('answering', 'ASK mode\nType question\non phone')
    questionInput.focus()
    return
  }
  appMode = 'ask'
  renderModeControls()
  window.clearTimeout(askProgressTimer)
  setState('thinking', 'ASK mode\nSearching...')
  askProgressTimer = window.setTimeout(() => {
    setState('thinking', 'ASK mode\nThinking...')
  }, 650)
  try {
    const result = await api('/api/memory/ask', {
      method: 'POST',
      body: JSON.stringify({
        question,
        ownerConfirmed,
      }),
    })
    window.clearTimeout(askProgressTimer)
    setState(result.state || 'answering', result.hud || shortAnswer(result.answer) || 'No memory')
  } catch (error) {
    window.clearTimeout(askProgressTimer)
    setState('locked', `Backend error\n${shortError(error)}`)
  }
}

async function startBrowserMic() {
  if (!navigator.mediaDevices?.getUserMedia) {
    setState('locked', 'Mic unavailable\nUse text')
    return
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    recorder = new MediaRecorder(stream, { mimeType: pickMimeType() })
    recorder.addEventListener('dataavailable', (event) => {
      if (event.data.size > 0) {
        void sendAudio(event.data)
      }
    })
    recorder.addEventListener('stop', () => {
      stream.getTracks().forEach((track) => track.stop())
      setState('sleeping', 'LIVE paused\nTap to store')
    })
    recorder.start(12000)
    micBtn.textContent = 'Stop storing'
    setState('listening', 'LIVE storing\nphone mic')
  } catch (error) {
    setState('locked', `Mic blocked\n${shortError(error)}`)
  }
}

function supportsSpeechRecognition() {
  return Boolean((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)
}

function startSpeechRecognition() {
  const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  if (!SpeechRecognition) {
    void startBrowserMic()
    return
  }

  speechRecognition = new SpeechRecognition()
  speechRecognition.continuous = true
  speechRecognition.interimResults = true
  speechRecognition.lang = 'en-US'

  let pending = ''
  let flushTimer: number | undefined

  speechRecognition.onresult = (event: any) => {
    let interim = ''
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const transcript = event.results[i][0]?.transcript || ''
      if (event.results[i].isFinal) {
        pending = `${pending} ${transcript}`.trim()
      } else {
        interim = `${interim} ${transcript}`.trim()
      }
    }

    const visible = pending || interim
    if (visible) {
      setState('listening', `Hearing...\n${visible.slice(-38)}`)
    }

    window.clearTimeout(flushTimer)
    flushTimer = window.setTimeout(() => {
      const text = pending.trim()
      pending = ''
      if (text) void handleSpokenText(text)
    }, 1800)
  }

  speechRecognition.onerror = (event: any) => {
    setState('locked', `Dictation error\n${String(event.error || 'failed').slice(0, 20)}`)
    stopSpeechRecognition()
  }

  speechRecognition.onend = () => {
    if (speechRecognition) {
      speechRecognition = null
      micBtn.textContent = 'Start storing'
      setState('sleeping', 'LIVE paused\nTap to store')
    }
  }

  speechRecognition.start()
  micBtn.textContent = 'Stop storing'
  setState('listening', 'LIVE storing\nbrowser speech')
}

function stopSpeechRecognition() {
  const current = speechRecognition
  speechRecognition = null
  micBtn.textContent = 'Start storing'
  current?.stop?.()
  setState('sleeping', 'LIVE paused\nTap to store')
}

async function handleSpokenText(text: string) {
  // Touch controls own mode switching; spoken audio is stored as memory only.
  await rememberText(text)
}

async function sendAudio(blob: Blob) {
  setState('thinking', 'Transcribing...')
  const form = new FormData()
  form.append('audio', blob, `presence-${Date.now()}.webm`)
  try {
    const result = await fetch(`${backendUrl}/api/transcribe`, {
      method: 'POST',
      body: form,
    }).then(async (response) => {
      if (!response.ok) throw new Error((await response.json()).error || response.statusText)
      return response.json()
    })
    setState(result.state || 'noticing', result.hud || 'Noted')
    await refreshMemories()
  } catch (error) {
    setState('locked', `STT error\n${shortError(error)}`)
  }
}

async function refreshMemories() {
  try {
    const result = await api('/api/memories')
    recentEntries = result.entries || []
    renderMemoryLog()
  } catch {
    // Keep UI usable offline.
  }
}

function renderMemoryLog() {
  if (!recentEntries.length) {
    memoryLog.innerHTML = '<div class="small">No memories yet.</div>'
    return
  }
  memoryLog.innerHTML = recentEntries
    .slice(0, 8)
    .map((entry) => {
      const title = `${entry.date} ${entry.hour}:${entry.minute}`
      const topics = entry.topics?.length ? ` · ${entry.topics.slice(0, 3).join(', ')}` : ''
      return `<div class="log-item"><strong>${escapeHtml(title)}</strong>${escapeHtml(topics)}<br />${escapeHtml(entry.summary)}</div>`
    })
    .join('')
}

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(`${backendUrl}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.error || response.statusText)
  }
  return response.json()
}

function formatHud(value: string) {
  return value
    .split(/\n+/)
    .map((line) => {
      const clean = line.trim()
      return clean.length > 32 ? `${clean.slice(0, 31).trim()}...` : clean
    })
    .filter(Boolean)
    .slice(0, 4)
    .join('\n')
}

function shortAnswer(value: unknown) {
  if (typeof value !== 'string') return ''
  const clean = value.replace(/\s+/g, ' ').trim()
  if (!clean) return ''
  const firstSentence = clean.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() || clean
  return formatHud(firstSentence)
}

function pickMimeType() {
  const options = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
  return options.find((type) => MediaRecorder.isTypeSupported(type)) || ''
}

function toUint8Array(value: unknown) {
  if (value instanceof Uint8Array) return value
  if (value instanceof ArrayBuffer) return new Uint8Array(value)
  if (Array.isArray(value)) return new Uint8Array(value)
  if (typeof value === 'string') {
    const binary = atob(value)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i)
    }
    return bytes
  }
  return new Uint8Array()
}

function concatUint8(chunks: Uint8Array[], totalBytes: number) {
  const output = new Uint8Array(totalBytes)
  let offset = 0
  for (const chunk of chunks) {
    output.set(chunk, offset)
    offset += chunk.byteLength
  }
  return output
}

function uint8ToBase64(bytes: Uint8Array) {
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize))
  }
  return btoa(binary)
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error('timeout')), timeoutMs)
    promise.then(
      (value) => {
        window.clearTimeout(timeout)
        resolve(value)
      },
      (error) => {
        window.clearTimeout(timeout)
        reject(error)
      },
    )
  })
}

function shortError(error: unknown) {
  return error instanceof Error ? error.message.slice(0, 34) : 'failed'
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }
    return map[char]
  })
}

function renderPhoneBuddy() {
  const ctx = phoneBuddy.getContext('2d')
  if (!ctx) return
  renderPixelAvatar(ctx, state)
}

function getGlassesAvatarText(nextState: AgentState) {
  if (nextState === 'sleeping') {
    return ['  z', ' .-.', ' /_\\'].join('\n')
  }

  if (nextState === 'thinking') {
    return [' ...', ' .-.', ' /?\\'].join('\n')
  }

  if (nextState === 'locked') {
    return [' .-.', ' /x\\', ' lock'].join('\n')
  }

  if (nextState === 'answering' || nextState === 'noticing') {
    return ['  *', ' .-.', ' /!\\'].join('\n')
  }

  return [' .-.', ' /o\\', '  |'].join('\n')
}

async function renderAvatarImage(nextState: AgentState): Promise<string> {
  const canvas = document.createElement('canvas')
  canvas.width = 120
  canvas.height = 96
  const ctx = canvas.getContext('2d')
  if (!ctx) return ''

  renderPixelAvatar(ctx, nextState)
  return canvas.toDataURL('image/png').split(',')[1] || ''
}

function renderPixelAvatar(ctx: CanvasRenderingContext2D, nextState: AgentState) {
  ctx.imageSmoothingEnabled = false
  ctx.fillStyle = '#000000'
  ctx.fillRect(0, 0, 120, 96)

  const sprite = getSprite(nextState)
  const palette = getPixelPalette(nextState)
  const scale = 6
  const spriteWidth = sprite[0].length * scale
  const spriteHeight = sprite.length * scale
  const offsetX = Math.floor((120 - spriteWidth) / 2)
  const offsetY = nextState === 'sleeping' ? 20 : 18

  for (let y = 0; y < sprite.length; y += 1) {
    const row = sprite[y]
    for (let x = 0; x < row.length; x += 1) {
      const color = palette[row[x]]
      if (!color) continue
      ctx.fillStyle = color
      ctx.fillRect(offsetX + x * scale, offsetY + y * scale, scale, scale)
    }
  }

  if (nextState === 'sleeping') {
    drawPixelZ(ctx, 90, 8, 3)
    drawPixelZ(ctx, 101, 0, 2)
  }
  if (nextState === 'thinking') {
    drawPixelDots(ctx, 92, 17, 3)
  }
  if (nextState === 'noticing' || nextState === 'answering') {
    drawPixelSpark(ctx, 94, 14, 2)
  }
}

function getSprite(nextState: AgentState) {
  if (nextState === 'sleeping') {
    return [
      '000200000000200',
      '002220000002220',
      '022222000022222',
      '122222222222221',
      '122244222442221',
      '122222222222221',
      '122222333222221',
      '012222222222210',
      '001111111111100',
      '000100000000100',
    ]
  }

  if (nextState === 'thinking') {
    return [
      '000200000000200',
      '002220000002220',
      '022222000022222',
      '122222222222221',
      '122224222422221',
      '122222222222221',
      '122222232222221',
      '122221222122221',
      '012211111112210',
      '001100000001100',
    ]
  }

  if (nextState === 'locked') {
    return [
      '000200000000200',
      '002220000002220',
      '022222000022222',
      '122222222222221',
      '122242222242221',
      '122224222422221',
      '012222222222210',
      '001222333222100',
      '000112222211000',
      '000001111100000',
    ]
  }

  if (nextState === 'noticing' || nextState === 'answering') {
    return [
      '000200000000200',
      '002220000002220',
      '022222000022222',
      '122222222222221',
      '122224222422221',
      '122222222222221',
      '122222333222221',
      '012222222222210',
      '000112222211000',
      '000001111100000',
    ]
  }

  return [
    '000200000000200',
    '002220000002220',
    '022222000022222',
    '122222222222221',
    '122224222422221',
    '122222222222221',
    '122222333222221',
    '012222222222210',
    '000122222221000',
    '000001111100000',
  ]
}

function getPixelPalette(nextState: AgentState): Record<string, string | undefined> {
  const base = {
    '0': undefined,
    '1': '#6b6b6b',
    '2': '#eeeeee',
    '3': '#aaaaaa',
    '4': '#000000',
    '5': '#ffffff',
  }

  if (nextState === 'locked') {
    return { ...base, '1': '#777777', '2': '#d8d8d8', '3': '#8c8c8c' }
  }
  if (nextState === 'thinking') {
    return { ...base, '1': '#7f7f7f', '2': '#e2e2e2', '3': '#bcbcbc' }
  }
  if (nextState === 'noticing' || nextState === 'answering') {
    return { ...base, '1': '#9a9a9a', '2': '#ffffff', '3': '#c7c7c7' }
  }
  return base
}

function drawPixelZ(ctx: CanvasRenderingContext2D, x: number, y: number, scale: number) {
  const rows = ['111', '001', '010', '100', '111']
  drawPixelGlyph(ctx, rows, x, y, scale, '#ffffff')
}

function drawPixelDots(ctx: CanvasRenderingContext2D, x: number, y: number, scale: number) {
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(x, y, scale, scale)
  ctx.fillRect(x + scale * 2, y + scale * 2, scale, scale)
  ctx.fillRect(x + scale * 4, y + scale * 4, scale, scale)
}

function drawPixelSpark(ctx: CanvasRenderingContext2D, x: number, y: number, scale: number) {
  const rows = ['010', '111', '010']
  drawPixelGlyph(ctx, rows, x, y, scale, '#ffffff')
}

function drawPixelGlyph(ctx: CanvasRenderingContext2D, rows: string[], x: number, y: number, scale: number, color: string) {
  ctx.fillStyle = color
  for (let row = 0; row < rows.length; row += 1) {
    for (let col = 0; col < rows[row].length; col += 1) {
      if (rows[row][col] === '1') {
        ctx.fillRect(x + col * scale, y + row * scale, scale, scale)
      }
    }
  }
}
