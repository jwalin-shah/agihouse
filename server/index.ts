import 'dotenv/config'

import cors from 'cors'
import express from 'express'
import multer from 'multer'
import OpenAI from 'openai'
import { createReadStream } from 'node:fs'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

type AgentState = 'sleeping' | 'listening' | 'thinking' | 'noticing' | 'answering' | 'locked'

type MemoryAnalysis = {
  summary: string
  people: string[]
  topics: string[]
  decisions: string[]
  tasks: string[]
  promises: string[]
  category: 'work' | 'personal' | 'health' | 'idea' | 'promise' | 'ambient'
  importance: number
  hud: string
}

type MemoryKind = 'instant' | 'five-minute' | 'hour'

type MemoryEntry = MemoryAnalysis & {
  id: string
  timestamp: string
  date: string
  hour: string
  minute: string
  speaker: string
  source: string
  transcript: string
  kind?: MemoryKind
  bucketStartMinute?: string
  sourceSegments?: string[]
}

type TranscriptSegment = {
  id: string
  timestamp: string
  date: string
  hour: string
  minute: string
  bucketStartMinute: string
  speaker: string
  source: string
  transcript: string
}

type MemoryIndex = {
  entries: MemoryEntry[]
  segments: TranscriptSegment[]
}

type PresenceCommand =
  | { type: 'none' }
  | { type: 'ask'; question: string }
  | { type: 'remember'; text: string }
  | { type: 'status' }
  | { type: 'sleep' }

const cwd = process.cwd()
const host = process.env.PRESENCE_BACKEND_HOST || '0.0.0.0'
const port = Number(process.env.PRESENCE_BACKEND_PORT || 8787)
const ownerName = process.env.OWNER_NAME || 'Owner'
const timezone = process.env.MEMORY_TIMEZONE || Intl.DateTimeFormat().resolvedOptions().timeZone
const vaultPath = process.env.OBSIDIAN_VAULT_PATH || path.join(cwd, 'vault')
const textModel = process.env.OPENAI_TEXT_MODEL || 'gpt-5.4-nano-2026-03-17'
const audioModel = process.env.OPENROUTER_AUDIO_MODEL || 'google/gemini-2.5-flash-lite'
const sttModel = process.env.OPENAI_STT_MODEL || 'gpt-4o-mini-transcribe'
const hasOpenAIKey = Boolean(process.env.OPENAI_API_KEY)
const hasOpenRouterKey = Boolean(process.env.OPENROUTER_API_KEY)

const openai = hasOpenAIKey
  ? new OpenAI({
      apiKey: process.env.OPENAI_API_KEY,
      baseURL: process.env.OPENAI_BASE_URL || undefined,
    })
  : null

const textClient = hasOpenRouterKey
  ? new OpenAI({
      apiKey: process.env.OPENROUTER_API_KEY,
      baseURL: 'https://openrouter.ai/api/v1',
      defaultHeaders: {
        'HTTP-Referer': process.env.OPENROUTER_SITE_URL || 'http://localhost:5173',
        'X-Title': process.env.OPENROUTER_APP_NAME || 'Presence G2',
      },
    })
  : openai

const proactiveCooldownMs = Number(process.env.PROACTIVE_COOLDOWN_MS || 4500)
let lastProactiveAt = 0
let lastProactiveKey = ''

const upload = multer({ dest: path.join(os.tmpdir(), 'presence-audio') })
const app = express()

app.use(cors())
app.use(express.json({ limit: '15mb' }))

app.get('/health', (_req, res) => {
  res.json({
    ok: true,
    state: 'ready',
    model: textModel,
    audioModel,
    sttModel,
    hasOpenAIKey,
    hasOpenRouterKey,
    hasTextKey: Boolean(textClient),
    hasSttKey: hasOpenAIKey || hasOpenRouterKey,
    ownerName,
    timezone,
    vaultPath,
  })
})

app.get('/api/memories', async (_req, res) => {
  const index = await readIndex()
  const entries = [...index.entries].sort((a, b) => b.timestamp.localeCompare(a.timestamp))
  res.json({
    entries: entries.slice(0, 20),
    count: index.entries.length,
    segments: index.segments.length,
  })
})

app.post('/api/memory/text', async (req, res, next) => {
  try {
    const text = cleanString(req.body?.text)
    if (!text) {
      res.status(400).json({ error: 'Missing text' })
      return
    }

    const speaker = cleanString(req.body?.speaker) || ownerName
    const source = cleanString(req.body?.source) || 'typed'
    const entry = await writeMemory(text, { speaker, source })
    res.json({
      state: entry.importance >= 0.55 ? 'noticing' satisfies AgentState : 'listening',
      hud: entry.hud,
      entry,
    })
  } catch (error) {
    next(error)
  }
})

app.post('/api/memory/ask', async (req, res, next) => {
  try {
    const question = cleanString(req.body?.question)
    if (!question) {
      res.status(400).json({ error: 'Missing question' })
      return
    }

    const ownerConfirmed = req.body?.ownerConfirmed !== false
    if (!ownerConfirmed) {
      res.json({
        state: 'locked' satisfies AgentState,
        hud: 'Memory locked\nUnknown voice',
        answer: 'I do not recognize this speaker, so I will not reveal private memory.',
        matches: [],
      })
      return
    }

    const index = await readIndex()
    const matches = searchMemories(question, index.entries).slice(0, 8)
    const answer = await answerQuestion(question, matches)
    res.json({
      state: 'answering' satisfies AgentState,
      ...answer,
      matches,
    })
  } catch (error) {
    next(error)
  }
})

app.post('/api/transcribe', upload.single('audio'), async (req, res, next) => {
  const file = req.file
  try {
    if (!file) {
      res.status(400).json({ error: 'Missing audio file' })
      return
    }
    if (!openai) {
      res.status(400).json({ error: 'OPENAI_API_KEY is not configured' })
      return
    }

    const transcription = await openai.audio.transcriptions.create({
      model: sttModel,
      file: createReadStream(file.path),
      language: process.env.STT_LANGUAGE || undefined,
    })

    const text = cleanString((transcription as { text?: string }).text)
    if (!text) {
      res.json({
        state: 'sleeping' satisfies AgentState,
        hud: 'zzz\nNo speech',
        text: '',
      })
      return
    }

    const entry = await writeMemory(text, {
      speaker: ownerName,
      source: 'phone-mic',
    })

    res.json({
      state: entry.importance >= 0.55 ? 'noticing' satisfies AgentState : 'listening',
      hud: entry.hud,
      text,
      entry,
    })
  } catch (error) {
    next(error)
  } finally {
    if (file) {
      await fs.unlink(file.path).catch(() => undefined)
    }
  }
})

app.post('/api/audio/pcm', async (req, res, next) => {
  try {
    const pcmBase64 = cleanString(req.body?.pcmBase64)
    const sampleRate = Number(req.body?.sampleRate || 16000)
    const channels = Number(req.body?.channels || 1)
    const source = cleanString(req.body?.source) || 'g2-mic'

    if (!pcmBase64) {
      res.status(400).json({ error: 'Missing pcmBase64' })
      return
    }
    if (!textClient || !hasOpenRouterKey) {
      res.status(400).json({ error: 'OPENROUTER_API_KEY is not configured for G2 audio transcription' })
      return
    }

    const pcm = Buffer.from(pcmBase64, 'base64')
    if (pcm.length < sampleRate) {
      res.json({
        state: 'listening' satisfies AgentState,
        hud: 'Listening...\nneed more audio',
        text: '',
      })
      return
    }

    if (isMostlySilentPcm16(pcm)) {
      res.json({
        state: 'sleeping' satisfies AgentState,
        hud: 'zzz\nNo speech',
        text: '',
      })
      return
    }

    const wav = makeWavFromPcm16(pcm, sampleRate, channels)
    const text = await transcribeWithOpenRouter(wav)

    if (!text || (source !== 'g2-ask' && isLowSignalTranscript(text))) {
      res.json({
        state: 'listening' satisfies AgentState,
        hud: 'Listening...\nNo words yet',
        text: '',
      })
      return
    }

    const pipeline = await writeAudioTranscriptPipeline(text, {
      speaker: ownerName,
      source,
    })
    const summary = pipeline.hour || pipeline.fiveMinute
    const segmentCount = pipeline.fiveMinute?.sourceSegments?.length || 1
    const proactive = await buildProactiveAssist(text, source)

    res.json({
      state: proactive ? 'answering' satisfies AgentState : summary && summary.importance >= 0.55 ? 'noticing' satisfies AgentState : 'listening',
      hud: proactive?.hud || summary?.hud || `1m transcript\n5m ${segmentCount}/5`,
      text,
      proactive,
      segment: pipeline.segment,
      fiveMinute: pipeline.fiveMinute,
      hour: pipeline.hour,
      entry: summary,
    })
  } catch (error) {
    next(error)
  }
})

app.post('/api/audio/command', async (req, res, next) => {
  try {
    res.status(410).json({
      error: 'Voice commands are disabled. Use touch controls and /api/memory/ask instead.',
    })
  } catch (error) {
    next(error)
  }
})

app.use((error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  const message = error instanceof Error ? error.message : 'Unknown server error'
  console.error(error)
  res.status(500).json({ error: message })
})

app.listen(port, host, async () => {
  await ensureVault()
  console.log(`Presence backend listening on http://${host}:${port}`)
  console.log(`Obsidian vault: ${vaultPath}`)
  if (hasOpenRouterKey) {
    console.log(`Text model via OpenRouter: ${textModel}`)
    console.log(`Audio model via OpenRouter: ${audioModel}`)
  } else if (!hasOpenAIKey) {
    console.log('No text model API key is configured; using local fallback for text memory only.')
  }
  if (!hasOpenAIKey) {
    console.log('OPENAI_API_KEY is not configured; /api/transcribe is disabled. Browser speech recognition can still be used.')
  }
})

async function transcribeWithOpenRouter(wav: Buffer) {
  if (!textClient) throw new Error('No text client configured')
  const response = await textClient.chat.completions.create({
    model: audioModel,
    messages: [
      {
        role: 'user',
        content: [
          {
            type: 'text',
            text: [
              'Transcribe this wearable microphone audio to English.',
              'Return only the transcript text.',
              'If there is no clear speech, return an empty string.',
            ].join(' '),
          },
          {
            type: 'input_audio',
            input_audio: {
              data: wav.toString('base64'),
              format: 'wav',
            },
          },
        ] as any,
      },
    ],
    temperature: 0,
  })

  const content = response.choices[0]?.message?.content || ''
  return cleanTranscript(content)
}

async function writeMemory(transcript: string, options: { speaker: string; source: string }): Promise<MemoryEntry> {
  const now = new Date()
  const parts = getZonedParts(now)
  const analysis = await analyzeTranscript(transcript)
  const entry: MemoryEntry = {
    id: randomUUID(),
    timestamp: now.toISOString(),
    date: parts.date,
    hour: parts.hour,
    minute: parts.minute,
    speaker: options.speaker,
    source: options.source,
    transcript,
    kind: 'instant',
    ...analysis,
  }

  const index = await readIndex()
  index.entries.push(entry)
  await writeIndex(index)
  await writeHourNote(entry.date, entry.hour, index.entries)
  await writeDailyNote(entry.date, index.entries)
  await touchEntityNotes(entry)

  return entry
}

async function writeAudioTranscriptPipeline(
  transcript: string,
  options: { speaker: string; source: string },
): Promise<{ segment: TranscriptSegment; fiveMinute?: MemoryEntry; hour?: MemoryEntry }> {
  const now = new Date()
  const parts = getZonedParts(now)
  const bucketStartMinute = getBucketStartMinute(parts.minute)
  const segment: TranscriptSegment = {
    id: randomUUID(),
    timestamp: now.toISOString(),
    date: parts.date,
    hour: parts.hour,
    minute: parts.minute,
    bucketStartMinute,
    speaker: options.speaker,
    source: options.source,
    transcript,
  }

  const index = await readIndex()
  index.segments.push(segment)

  await writeStreamNote(segment.date, segment.hour, index.segments)

  const fiveMinute = await createFiveMinuteSummary(segment.date, segment.hour, bucketStartMinute, index)
  if (fiveMinute) {
    upsertMemoryEntry(index.entries, fiveMinute)
    await writeFiveMinuteNote(fiveMinute)
    await touchEntityNotes(fiveMinute)
  }

  const hour = await createHourSummary(segment.date, segment.hour, index)
  if (hour) {
    upsertMemoryEntry(index.entries, hour)
    await touchEntityNotes(hour)
  }

  await writeIndex(index)
  await writeHourNote(segment.date, segment.hour, index.entries)
  await writeDailyNote(segment.date, index.entries)

  return { segment, fiveMinute, hour }
}

async function handlePresenceCommand(command: PresenceCommand, transcript: string) {
  if (command.type === 'ask') {
    const index = await readIndex()
    const matches = searchMemories(command.question, index.entries).slice(0, 10)
    const answer = await answerQuestion(command.question, matches)
    return {
      state: 'answering' satisfies AgentState,
      ...answer,
      text: transcript,
      question: command.question,
      matches,
      command: 'ask',
    }
  }

  if (command.type === 'remember') {
    const entry = await writeMemory(command.text, { speaker: ownerName, source: 'voice-command' })
    return {
      state: entry.importance >= 0.55 ? 'noticing' satisfies AgentState : 'listening',
      hud: entry.hud,
      text: transcript,
      entry,
      command: 'remember',
    }
  }

  if (command.type === 'status') {
    const index = await readIndex()
    const latestHour = [...index.entries]
      .filter((entry) => entry.kind === 'hour')
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp))[0]
    const hud = latestHour ? formatHud(`Latest hour\n${latestHour.summary}`) : 'No hour memory\nyet'
    return {
      state: 'answering' satisfies AgentState,
      hud,
      answer: latestHour?.summary || 'I do not have an hour memory yet.',
      text: transcript,
      command: 'status',
    }
  }

  if (command.type === 'sleep') {
    return {
      state: 'sleeping' satisfies AgentState,
      hud: 'zzz\nPresence sleeping',
      answer: 'Sleeping.',
      text: transcript,
      command: 'sleep',
    }
  }

  return {
    state: 'listening' satisfies AgentState,
    hud: 'Listening...\nG2 mic',
    text: transcript,
    command: 'none',
  }
}

async function createFiveMinuteSummary(
  date: string,
  hour: string,
  bucketStartMinute: string,
  index: MemoryIndex,
): Promise<MemoryEntry | undefined> {
  const segments = index.segments
    .filter((segment) => segment.date === date && segment.hour === hour && segment.bucketStartMinute === bucketStartMinute)
    .sort(compareTranscriptSegments)
  if (!segments.length) return undefined

  const transcript = segments.map((segment) => `[${segment.hour}:${segment.minute}] ${segment.transcript}`).join('\n')
  const analysis = await analyzeTranscript(
    [
      `This is a rolling five-minute wearable memory window for ${date} ${hour}:${bucketStartMinute}.`,
      'Summarize only useful, concrete information from the transcript stream.',
      transcript,
    ].join('\n\n'),
  )

  return {
    id: fiveMinuteId(date, hour, bucketStartMinute),
    timestamp: new Date().toISOString(),
    date,
    hour,
    minute: bucketStartMinute,
    bucketStartMinute,
    speaker: ownerName,
    source: 'g2-5m-summary',
    transcript,
    kind: 'five-minute',
    sourceSegments: segments.map((segment) => segment.id),
    ...analysis,
    hud: formatHud(`5m summary\n${analysis.hud}`),
  }
}

async function createHourSummary(date: string, hour: string, index: MemoryIndex): Promise<MemoryEntry | undefined> {
  const fiveMinuteEntries = index.entries
    .filter((entry) => entry.kind === 'five-minute' && entry.date === date && entry.hour === hour)
    .sort(compareMemoryEntries)
  if (!fiveMinuteEntries.length) return undefined

  const transcript = fiveMinuteEntries
    .map((entry) => {
      const label = formatFiveMinuteLabel(entry.bucketStartMinute || entry.minute)
      return [`[${hour}:${label}] ${entry.summary}`, ...entry.tasks.map((task) => `Task: ${task}`), ...entry.promises.map((promise) => `Promise: ${promise}`)].join('\n')
    })
    .join('\n\n')
  const analysis = await analyzeTranscript(
    [
      `This is an hour-level memory made from five-minute summaries for ${date} ${hour}:00.`,
      'Create the best durable memory for later recall. Prefer decisions, promises, tasks, people, and important context.',
      transcript,
    ].join('\n\n'),
  )

  return {
    id: hourId(date, hour),
    timestamp: new Date().toISOString(),
    date,
    hour,
    minute: '00',
    speaker: ownerName,
    source: 'g2-hour-summary',
    transcript,
    kind: 'hour',
    sourceSegments: fiveMinuteEntries.map((entry) => entry.id),
    ...analysis,
    hud: formatHud(`Hour memory\n${analysis.hud}`),
  }
}

async function analyzeTranscript(transcript: string): Promise<MemoryAnalysis> {
  const fallback = fallbackAnalysis(transcript)
  if (!textClient) return fallback

  const prompt = [
    'Analyze this transcript snippet for a private wearable memory agent.',
    'Return strict JSON with keys: summary, people, topics, decisions, tasks, promises, category, importance, hud.',
    'Rules:',
    '- summary: one concise sentence in English.',
    '- people/topics/decisions/tasks/promises: arrays of short strings.',
    '- category: one of work, personal, health, idea, promise, ambient.',
    '- importance: number from 0 to 1.',
    '- hud: at most 3 short lines, max about 20 chars per line, no markdown.',
    '- Extract only concrete memories. Ignore filler.',
    '',
    `Transcript:\n${transcript}`,
  ].join('\n')

  try {
    const text = await runTextModel('You are a precise memory extraction engine. Return JSON only.', prompt)
    const parsed = parseJsonObject(text) as Partial<MemoryAnalysis>
    return normalizeAnalysis(parsed, fallback)
  } catch (error) {
    console.warn('Falling back after analysis error:', error)
    return fallback
  }
}

async function answerQuestion(question: string, matches: MemoryEntry[]) {
  const fallback = {
    hud: fallbackHudAnswer(question, matches),
    answer: fallbackFullAnswer(question, matches),
  }

  if (!textClient) return fallback

  const memoryText = matches.length
    ? matches
        .map((entry) => {
          return [
            `Time: ${entry.date} ${entry.hour}:${entry.minute}`,
            `Summary: ${entry.summary}`,
            `People: ${entry.people.join(', ') || 'none'}`,
            `Topics: ${entry.topics.join(', ') || 'none'}`,
            `Decisions: ${entry.decisions.join('; ') || 'none'}`,
            `Tasks: ${entry.tasks.join('; ') || 'none'}`,
            `Promises: ${entry.promises.join('; ') || 'none'}`,
          ].join('\n')
        })
        .join('\n\n')
    : 'No matching memories found.'

  const prompt = [
    'Answer a private memory question using only the provided memories.',
    'Return strict JSON with keys: hud, answer.',
    'hud must be at most 3 short lines and suitable for a 576x288 glasses display.',
    'answer must be one very short sentence, ideally under 12 words.',
    'If the memories do not contain the answer, say you do not know.',
    '',
    `Question: ${question}`,
    '',
    `Memories:\n${memoryText}`,
  ].join('\n')

  try {
    const text = await runTextModel('You answer from personal memory. Return JSON only.', prompt)
    const parsed = parseJsonObject(text) as { hud?: string; answer?: string }
    return {
      hud: formatHud(parsed.hud || fallback.hud),
      answer: shortTextAnswer(parsed.answer) || fallback.answer,
    }
  } catch (error) {
    console.warn('Falling back after answer error:', error)
    return fallback
  }
}

async function buildProactiveAssist(transcript: string, source: string) {
  const cue = detectProactiveCue(transcript, source)
  if (!cue) return undefined
  if (shouldSuppressAssist(transcript)) return undefined
  if (isProactiveCoolingDown(cue.query)) return undefined

  const index = await readIndex()
  const matches = cue.tool === 'memory' ? searchMemories(cue.query, index.entries).slice(0, 5) : []
  const webSnippets = cue.tool === 'web' ? await searchWebSnippets(cue.query) : []
  const fallback = {
    hud: formatSmartGlassesHud(cue.fallback, cue.kind),
    answer: cue.fallback,
    kind: cue.kind,
    query: cue.query,
    tool: cue.tool,
  }

  if (!textClient) return fallback

  const memoryText = matches.length
    ? matches
        .map((entry) => {
          const actions = [...entry.tasks, ...entry.decisions, ...entry.promises].slice(0, 2).join('; ')
          return `${entry.date} ${entry.hour}:${entry.minute} ${entry.summary}${actions ? ` | ${actions}` : ''}`
        })
        .join('\n')
    : cue.tool === 'memory'
      ? 'No relevant memories.'
      : 'Memory not needed.'
  const webText = webSnippets.length ? webSnippets.map((item) => `- ${item}`).join('\n') : cue.tool === 'web' ? 'No web result available.' : 'Web not needed.'

  const prompt = [
    'You are Presence, a proactive assistant for Even G2 smart glasses.',
    'The display is tiny: fixed font, 576x288, no HTML, no font-size control.',
    'Be useful like a HUD whisper, not a chatbot.',
    'Return strict JSON with keys: hud, answer.',
    'answer: max 8 words. Prefer verb-first suggestions.',
    'hud: max 4 lines, each line max 24 characters.',
    'No markdown. No greetings. No caveats. No long explanations.',
    'If the speaker asks what to say, give only the next phrase to say.',
    'If answering a direct question, answer directly using memory when relevant.',
    'Do not answer questions from other people unless the wearer seems to be asking.',
    '',
    `Cue type: ${cue.kind}`,
    `Tool: ${cue.tool}`,
    `User speech:\n${transcript}`,
    '',
    `Relevant memories:\n${memoryText}`,
    '',
    `Web snippets:\n${webText}`,
  ].join('\n')

  try {
    const text = await runTextModel('Be brief, practical, and calm. Return JSON only.', prompt)
    const parsed = parseJsonObject(text) as { hud?: string; answer?: string }
    const answer = smartGlassesAnswer(parsed.answer) || fallback.answer
    const result = {
      hud: formatSmartGlassesHud(parsed.hud || answer, cue.kind),
      answer,
      kind: cue.kind,
      query: cue.query,
      tool: cue.tool,
    }
    markProactiveFired(cue.query)
    return result
  } catch (error) {
    console.warn('Falling back after proactive assist error:', error)
    markProactiveFired(cue.query)
    return fallback
  }
}

async function runTextModel(system: string, user: string): Promise<string> {
  if (!textClient) throw new Error('No text model API key is configured')

  if (hasOpenRouterKey) {
    const response = await textClient.chat.completions.create({
      model: textModel,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user },
      ],
      temperature: 0,
    })

    return response.choices[0]?.message?.content || ''
  }

  const response = await textClient.responses.create({
    model: textModel,
    input: [
      { role: 'system', content: system },
      { role: 'user', content: user },
    ],
  })

  const outputText = (response as { output_text?: string }).output_text
  if (outputText) return outputText

  const output = (response as { output?: Array<{ content?: Array<{ text?: string }> }> }).output || []
  return output.flatMap((item) => item.content || []).map((item) => item.text || '').join('\n')
}

async function searchWebSnippets(query: string): Promise<string[]> {
  const q = cleanString(query)
  if (!q) return []
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 3500)
  try {
    const url = `https://duckduckgo.com/html/?q=${encodeURIComponent(q)}`
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        'User-Agent': 'PresenceG2/0.1',
      },
    })
    if (!response.ok) return []
    const html = await response.text()
    return Array.from(html.matchAll(/class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g))
      .map((match) => decodeHtml(match[1] || ''))
      .map((text) => cleanString(text.replace(/<[^>]+>/g, ' ')))
      .filter(Boolean)
      .slice(0, 3)
  } catch {
    return []
  } finally {
    clearTimeout(timeout)
  }
}

async function ensureVault() {
  await fs.mkdir(path.join(vaultPath, 'Daily'), { recursive: true })
  await fs.mkdir(path.join(vaultPath, 'Hours'), { recursive: true })
  await fs.mkdir(path.join(vaultPath, 'Streams'), { recursive: true })
  await fs.mkdir(path.join(vaultPath, 'FiveMinute'), { recursive: true })
  await fs.mkdir(path.join(vaultPath, 'People'), { recursive: true })
  await fs.mkdir(path.join(vaultPath, 'Topics'), { recursive: true })
  await fs.mkdir(path.join(vaultPath, 'Index'), { recursive: true })
  await readIndex()
}

async function readIndex(): Promise<MemoryIndex> {
  const file = indexPath()
  try {
    const text = await fs.readFile(file, 'utf8')
    const parsed = JSON.parse(text) as MemoryIndex
    return {
      entries: Array.isArray(parsed.entries) ? parsed.entries : [],
      segments: Array.isArray(parsed.segments) ? parsed.segments : [],
    }
  } catch {
    const initial: MemoryIndex = { entries: [], segments: [] }
    await fs.mkdir(path.dirname(file), { recursive: true })
    await fs.writeFile(file, JSON.stringify(initial, null, 2))
    return initial
  }
}

async function writeIndex(index: MemoryIndex) {
  await fs.mkdir(path.dirname(indexPath()), { recursive: true })
  await fs.writeFile(indexPath(), JSON.stringify(index, null, 2))
}

async function writeStreamNote(date: string, hour: string, segments: TranscriptSegment[]) {
  const hourSegments = segments.filter((segment) => segment.date === date && segment.hour === hour).sort(compareTranscriptSegments)
  const dir = path.join(vaultPath, 'Streams', date)
  await fs.mkdir(dir, { recursive: true })

  const body = [
    '---',
    'type: transcript-stream',
    `date: ${date}`,
    `hour: ${hour}`,
    `updated: ${new Date().toISOString()}`,
    '---',
    '',
    `# Transcript Stream ${date} ${hour}:00`,
    '',
    'Raw one-minute STT segments. These are source material, not durable memory.',
    '',
    ...hourSegments.flatMap((segment) => [
      `## ${segment.hour}:${segment.minute}`,
      '',
      `source: ${segment.source}`,
      `window: ${formatFiveMinuteLabel(segment.bucketStartMinute)}`,
      '',
      segment.transcript,
      '',
    ]),
  ].join('\n')

  await fs.writeFile(path.join(dir, `${hour}.md`), body)
}

async function writeFiveMinuteNote(entry: MemoryEntry) {
  const bucket = entry.bucketStartMinute || entry.minute
  const dir = path.join(vaultPath, 'FiveMinute', entry.date)
  await fs.mkdir(dir, { recursive: true })

  const body = [
    '---',
    'type: memory-five-minute',
    `date: ${entry.date}`,
    `hour: ${entry.hour}`,
    `window: ${formatFiveMinuteLabel(bucket)}`,
    `updated: ${entry.timestamp}`,
    '---',
    '',
    `# ${entry.date} ${entry.hour}:${formatFiveMinuteLabel(bucket)}`,
    '',
    '## Summary',
    entry.summary,
    '',
    '## People',
    ...toLinkedList('People', entry.people),
    '',
    '## Topics',
    ...toLinkedList('Topics', entry.topics),
    '',
    '## Decisions',
    ...toPlainList(entry.decisions),
    '',
    '## Tasks',
    ...toTaskList(entry.tasks),
    '',
    '## Promises',
    ...toPlainList(entry.promises),
    '',
    '## Source Transcript',
    '',
    '<details>',
    '<summary>One-minute transcript stream</summary>',
    '',
    entry.transcript,
    '',
    '</details>',
    '',
  ].join('\n')

  await fs.writeFile(path.join(dir, `${entry.hour}-${bucket}.md`), body)
}

async function writeHourNote(date: string, hour: string, entries: MemoryEntry[]) {
  const hourEntries = entries.filter((entry) => entry.date === date && entry.hour === hour)
  const hourSummary = hourEntries.find((entry) => entry.kind === 'hour')
  const fiveMinuteEntries = hourEntries.filter((entry) => entry.kind === 'five-minute').sort(compareMemoryEntries)
  const instantEntries = hourEntries.filter((entry) => !entry.kind || entry.kind === 'instant').sort(compareMemoryEntries)
  const dir = path.join(vaultPath, 'Hours', date)
  await fs.mkdir(dir, { recursive: true })

  const people = unique(hourEntries.flatMap((entry) => entry.people))
  const topics = unique(hourEntries.flatMap((entry) => entry.topics))
  const decisions = unique(hourEntries.flatMap((entry) => entry.decisions))
  const tasks = unique(hourEntries.flatMap((entry) => entry.tasks))
  const promises = unique(hourEntries.flatMap((entry) => entry.promises))

  const body = [
    '---',
    'type: memory-hour',
    `date: ${date}`,
    `hour: ${hour}`,
    `updated: ${new Date().toISOString()}`,
    '---',
    '',
    `# ${date} ${hour}:00`,
    '',
    '## Hour Summary',
    hourSummary?.summary || 'Still building this hour.',
    '',
    '## Five-Minute Summaries',
    ...(fiveMinuteEntries.length
      ? fiveMinuteEntries.map((entry) => {
          const bucket = entry.bucketStartMinute || entry.minute
          return `- [[FiveMinute/${date}/${hour}-${bucket}|${hour}:${formatFiveMinuteLabel(bucket)}]] — ${entry.summary}`
        })
      : ['- None yet']),
    '',
    '## Instant Notes',
    ...(instantEntries.length ? instantEntries.map((entry) => `- ${entry.hour}:${entry.minute} — ${entry.summary}`) : ['- None']),
    '',
    '## People',
    ...toLinkedList('People', people),
    '',
    '## Topics',
    ...toLinkedList('Topics', topics),
    '',
    '## Decisions',
    ...toPlainList(decisions),
    '',
    '## Tasks',
    ...toTaskList(tasks),
    '',
    '## Promises',
    ...toPlainList(promises),
    '',
    '## Windows',
    ...fiveMinuteEntries.flatMap((entry) => [
      '',
      `### ${entry.hour}:${formatFiveMinuteLabel(entry.bucketStartMinute || entry.minute)}`,
      '',
      entry.summary,
      '',
      '<details>',
      '<summary>Transcript</summary>',
      '',
      entry.transcript,
      '',
      '</details>',
    ]),
    '',
  ].join('\n')

  await fs.writeFile(path.join(dir, `${hour}.md`), body)
}

async function writeDailyNote(date: string, entries: MemoryEntry[]) {
  await fs.mkdir(path.join(vaultPath, 'Daily'), { recursive: true })
  const dayEntries = entries.filter((entry) => entry.date === date)
  const hours = unique(dayEntries.map((entry) => entry.hour)).sort()
  const people = unique(dayEntries.flatMap((entry) => entry.people))
  const topics = unique(dayEntries.flatMap((entry) => entry.topics))
  const decisions = unique(dayEntries.flatMap((entry) => entry.decisions))
  const tasks = unique(dayEntries.flatMap((entry) => entry.tasks))
  const promises = unique(dayEntries.flatMap((entry) => entry.promises))

  const body = [
    '---',
    'type: memory-day',
    `date: ${date}`,
    `updated: ${new Date().toISOString()}`,
    '---',
    '',
    `# ${date}`,
    '',
    '## Timeline',
    ...hours.map((hour) => `- [[Hours/${date}/${hour}|${hour}:00]]`),
    '',
    '## People',
    ...toLinkedList('People', people),
    '',
    '## Topics',
    ...toLinkedList('Topics', topics),
    '',
    '## Decisions',
    ...toPlainList(decisions),
    '',
    '## Tasks',
    ...toTaskList(tasks),
    '',
    '## Promises',
    ...toPlainList(promises),
    '',
  ].join('\n')

  await fs.writeFile(path.join(vaultPath, 'Daily', `${date}.md`), body)
}

async function touchEntityNotes(entry: MemoryEntry) {
  await Promise.all([
    ...entry.people.map((person) => touchEntityNote('People', person, entry)),
    ...entry.topics.map((topic) => touchEntityNote('Topics', topic, entry)),
  ])
}

async function touchEntityNote(kind: 'People' | 'Topics', name: string, entry: MemoryEntry) {
  const safe = safeFileName(name)
  if (!safe) return
  await fs.mkdir(path.join(vaultPath, kind), { recursive: true })
  const file = path.join(vaultPath, kind, `${safe}.md`)
  try {
    await fs.access(file)
  } catch {
    const body = [
      `# ${name}`,
      '',
      '## Mentions',
      `- [[Hours/${entry.date}/${entry.hour}|${entry.date} ${entry.hour}:00]]`,
      '',
    ].join('\n')
    await fs.writeFile(file, body)
    return
  }

  const existing = await fs.readFile(file, 'utf8')
  const mention = `- [[Hours/${entry.date}/${entry.hour}|${entry.date} ${entry.hour}:00]]`
  if (!existing.includes(mention)) {
    await fs.writeFile(file, `${existing.trimEnd()}\n${mention}\n`)
  }
}

function searchMemories(query: string, entries: MemoryEntry[]) {
  const terms = tokenize(query).filter((term) => !QUESTION_STOPWORDS.has(term))
  const sortedEntries = [...entries].sort((a, b) => b.timestamp.localeCompare(a.timestamp))
  if (!terms.length) return sortedEntries.filter(isDurableMemory).slice(0, 10)

  const wantsPromise = /\b(promise|promised|commit|committed|said i would|follow up)\b/i.test(query)
  const wantsTask = /\b(todo|task|need|should|remind|action|next)\b/i.test(query)
  const wantsRecent = /\b(recent|today|latest|last|hour|now|happen|happened)\b/i.test(query)

  const ranked = sortedEntries
    .map((entry) => {
      const haystack = [
        entry.summary,
        entry.transcript,
        entry.people.join(' '),
        entry.topics.join(' '),
        entry.decisions.join(' '),
        entry.tasks.join(' '),
        entry.promises.join(' '),
      ].join(' ')
      const tokens = tokenize(haystack)
      let score = terms.reduce((sum, term) => sum + tokens.filter((token) => token === term).length, 0)
      if (wantsPromise && entry.promises.length) score += 8
      if (wantsTask && entry.tasks.length) score += 5
      if (wantsRecent && entry.kind === 'hour') score += 5
      if (entry.kind === 'hour') score += 2
      if (entry.kind === 'five-minute') score += 1
      return { entry, score }
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((item) => item.entry)

  if (ranked.length) return ranked
  return sortedEntries.filter(isDurableMemory).slice(0, 10)
}

function fallbackAnalysis(transcript: string): MemoryAnalysis {
  const summary = summarizeFallback(transcript)
  const tasks = extractActionLines(transcript)
  const promises = transcript.match(/\b(promise|promised|will send|will do|follow up)\b/i) ? tasks : []
  const topics = extractTopics(transcript)
  const category: MemoryAnalysis['category'] = promises.length ? 'promise' : tasks.length ? 'work' : topics.length ? 'idea' : 'ambient'
  const hud = tasks[0] ? `Noted:\n${clipLine(tasks[0])}` : `Noted:\n${clipLine(summary)}`
  return {
    summary,
    people: [ownerName],
    topics,
    decisions: transcript.match(/\b(decide|decided|choose|submit|use|skip)\b/i) ? [summary] : [],
    tasks,
    promises,
    category,
    importance: tasks.length || promises.length ? 0.78 : 0.48,
    hud: formatHud(hud),
  }
}

function normalizeAnalysis(parsed: Partial<MemoryAnalysis>, fallback: MemoryAnalysis): MemoryAnalysis {
  return {
    summary: cleanString(parsed.summary) || fallback.summary,
    people: cleanList(parsed.people).slice(0, 8),
    topics: cleanList(parsed.topics).slice(0, 12),
    decisions: cleanList(parsed.decisions).slice(0, 8),
    tasks: cleanList(parsed.tasks).slice(0, 10),
    promises: cleanList(parsed.promises).slice(0, 8),
    category: normalizeCategory(parsed.category) || fallback.category,
    importance: clamp(Number(parsed.importance ?? fallback.importance), 0, 1),
    hud: formatHud(cleanString(parsed.hud) || fallback.hud),
  }
}

function normalizeCategory(category: unknown): MemoryAnalysis['category'] | undefined {
  const value = typeof category === 'string' ? category : ''
  if (['work', 'personal', 'health', 'idea', 'promise', 'ambient'].includes(value)) {
    return value as MemoryAnalysis['category']
  }
  return undefined
}

function parseJsonObject(text: string): unknown {
  const cleaned = text.replace(/^```(?:json)?/i, '').replace(/```$/i, '').trim()
  const start = cleaned.indexOf('{')
  const end = cleaned.lastIndexOf('}')
  if (start === -1 || end === -1 || end <= start) throw new Error('No JSON object in model output')
  return JSON.parse(cleaned.slice(start, end + 1))
}

function fallbackHudAnswer(question: string, matches: MemoryEntry[]) {
  if (!matches.length) return 'I do not know\nyet'
  const best = matches[0]
  const task = best.tasks[0] || best.decisions[0] || best.promises[0] || best.summary
  return formatHud(`${clipLine(question)}\n${clipLine(task)}`)
}

function fallbackFullAnswer(_question: string, matches: MemoryEntry[]) {
  if (!matches.length) return 'I do not have a matching memory yet.'
  return shortTextAnswer(matches[0].summary) || matches[0].summary
}

function detectProactiveCue(transcript: string, source: string) {
  const text = cleanString(transcript)
  if (!text) return undefined
  const isAskStream = source === 'g2-ask'
  const question = extractLastQuestion(text)
  const wearerCue = looksLikeWearerCue(text)
  if (question && (wearerCue || isAskStream)) {
    return {
      kind: 'answer',
      query: question,
      tool: chooseAssistTool(question),
      fallback: 'Answer briefly.',
    }
  }

  const needsHelp = /\b(what should i say|what do i say|how should i respond|help me|suggest|suggestion|stuck|not sure|unsure|presentation|pitch|demo|explain|respond|next)\b/i.test(text)
  if (needsHelp || (isAskStream && wearerCue)) {
    return {
      kind: 'suggestion',
      query: text,
      tool: chooseAssistTool(text),
      fallback: 'Say the outcome first.',
    }
  }

  return undefined
}

function shouldSuppressAssist(transcript: string) {
  return /\b(off the record|between us|don't tell|do not tell|therapist|lawyer|hr|confidential)\b/i.test(transcript)
}

function looksLikeWearerCue(text: string) {
  return /\b(i|i'm|im|me|my|we|we're|our|us|should i|should we|can you|could you|help me|what should|presence)\b/i.test(text)
}

function chooseAssistTool(query: string): 'memory' | 'web' | 'none' {
  if (/\b(did i|what did i|promise|remember|memory|decide|task|todo|who|when did we|last time|earlier|today)\b/i.test(query)) {
    return 'memory'
  }
  if (/\b(latest|current|news|search web|look up|online|who is|what is)\b/i.test(query)) {
    return 'web'
  }
  return 'memory'
}

function isProactiveCoolingDown(query: string) {
  const key = tokenize(query).slice(0, 6).join(' ')
  const now = Date.now()
  return Boolean(key && key === lastProactiveKey && now - lastProactiveAt < proactiveCooldownMs)
}

function markProactiveFired(query: string) {
  lastProactiveKey = tokenize(query).slice(0, 6).join(' ')
  lastProactiveAt = Date.now()
}

function extractLastQuestion(text: string) {
  const explicit = text
    .split(/(?<=[.!?])\s+|\n+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .reverse()
    .find((sentence) => /\?$/.test(sentence))
  if (explicit) return explicit

  const implied = text.match(/\b(what|why|how|when|where|who|which|can you|could you|should i|do i|did i|am i|is it|are we|will we)\b[^.?!]{4,160}/i)
  return cleanString(implied?.[0] || '')
}

function shortTextAnswer(value: unknown) {
  if (typeof value !== 'string') return ''
  const clean = cleanString(value)
  if (!clean) return ''
  const firstSentence = clean.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() || clean
  const words = firstSentence.split(/\s+/)
  return words.length > 10 ? `${words.slice(0, 10).join(' ')}...` : firstSentence
}

function smartGlassesAnswer(value: unknown) {
  if (typeof value !== 'string') return ''
  const clean = cleanString(value)
    .replace(/^try:\s*/i, '')
    .replace(/^say:\s*/i, '')
    .replace(/^you can say\s*/i, '')
    .replace(/^I think\s+/i, '')
    .replace(/\s*\([^)]*\)\s*/g, ' ')
    .trim()
  if (!clean) return ''
  const sentence = clean.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() || clean
  const words = sentence.split(/\s+/).filter(Boolean)
  return words.slice(0, 8).join(' ').replace(/[.!?]+$/g, '')
}

function formatSmartGlassesHud(value: string, kind: string) {
  const prefix = kind === 'answer' ? 'ANS' : 'TRY'
  const answer = smartGlassesAnswer(value) || cleanString(value)
  const lines = wrapHudWords(answer, 24).slice(0, 3)
  return formatHud([prefix, ...lines].join('\n'))
}

function wrapHudWords(value: string, width: number) {
  const words = cleanString(value).split(/\s+/).filter(Boolean)
  const lines: string[] = []
  let line = ''
  for (const word of words) {
    const next = line ? `${line} ${word}` : word
    if (next.length > width && line) {
      lines.push(line)
      line = word.slice(0, width)
    } else {
      line = next.slice(0, width)
    }
  }
  if (line) lines.push(line)
  return lines.length ? lines : ['I do not know yet']
}

function makeWavFromPcm16(pcm: Buffer, sampleRate: number, channels: number) {
  const header = Buffer.alloc(44)
  const byteRate = sampleRate * channels * 2
  const blockAlign = channels * 2

  header.write('RIFF', 0)
  header.writeUInt32LE(36 + pcm.length, 4)
  header.write('WAVE', 8)
  header.write('fmt ', 12)
  header.writeUInt32LE(16, 16)
  header.writeUInt16LE(1, 20)
  header.writeUInt16LE(channels, 22)
  header.writeUInt32LE(sampleRate, 24)
  header.writeUInt32LE(byteRate, 28)
  header.writeUInt16LE(blockAlign, 32)
  header.writeUInt16LE(16, 34)
  header.write('data', 36)
  header.writeUInt32LE(pcm.length, 40)

  return Buffer.concat([header, pcm])
}

function isMostlySilentPcm16(pcm: Buffer) {
  if (pcm.length < 2) return true
  let sumSquares = 0
  let peak = 0
  let count = 0
  for (let i = 0; i + 1 < pcm.length; i += 2) {
    const sample = pcm.readInt16LE(i)
    const abs = Math.abs(sample)
    peak = Math.max(peak, abs)
    sumSquares += sample * sample
    count += 1
  }
  const rms = Math.sqrt(sumSquares / Math.max(1, count))
  return peak < 500 || rms < 120
}

function cleanTranscript(value: string) {
  const cleaned = value
    .replace(/^```(?:text)?/i, '')
    .replace(/```$/i, '')
    .replace(/^transcript:\s*/i, '')
    .replace(/^["']|["']$/g, '')
    .trim()
  if (/^(no clear speech|empty string|\[?silence\]?|none)$/i.test(cleaned)) return ''
  if (/\b(audio|speech|transcript|transcribe)\b/i.test(cleaned)) {
    const unusablePatterns = [
      /\btoo noisy\b/i,
      /\bunclear\b/i,
      /\bcannot provide\b/i,
      /\bcan't provide\b/i,
      /\bcannot transcribe\b/i,
      /\bcan't transcribe\b/i,
      /\bunable to transcribe\b/i,
      /\bno intelligible\b/i,
      /\bnot enough clear speech\b/i,
    ]
    if (unusablePatterns.some((pattern) => pattern.test(cleaned))) return ''
  }
  return cleaned.slice(0, Number(process.env.MAX_TRANSCRIPT_CHARS || 5000)).trim()
}

function isLowSignalTranscript(text: string) {
  const normalized = text.toLowerCase().replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim()
  const words = normalized.split(' ').filter(Boolean)
  if (words.length < 3) return true
  if (words.length > 80) {
    const uniqueRatio = new Set(words).size / words.length
    if (uniqueRatio < 0.18) return true
  }
  const phraseCounts = new Map<string, number>()
  for (let i = 0; i + 2 < words.length; i += 1) {
    const phrase = `${words[i]} ${words[i + 1]} ${words[i + 2]}`
    phraseCounts.set(phrase, (phraseCounts.get(phrase) || 0) + 1)
  }
  return Array.from(phraseCounts.values()).some((count) => count >= 12)
}

function extractPresenceQuestion(text: string) {
  const command = extractPresenceCommand(text)
  return command.type === 'ask' ? command.question : ''
}

function extractPresenceCommand(text: string): PresenceCommand {
  const normalized = text.replace(/\s+/g, ' ').trim()
  const named = normalized.match(/\b(?:hey\s+)?presence\b[:,]?\s+(.+)/i)
  const asked = normalized.match(/\bask\s+(?:presence\s+)?(.+)/i)
  const body = cleanCommandText(named?.[1] || asked?.[1] || '')
  if (!body) return { type: 'none' }

  if (/^(?:go to )?sleep\b|^stop listening\b|^pause\b/i.test(body)) return { type: 'sleep' }
  if (/^(?:status|what happened|summari[sz]e|latest|recap)\b/i.test(body)) return { type: 'status' }

  const remember = body.match(/^(?:remember|note|save|write down)\s+(.+)/i)
  if (remember?.[1]) return { type: 'remember', text: cleanCommandText(remember[1]) }

  const question = body
    .replace(/^(?:ask|tell me|answer|search|look up)\s+/i, '')
    .replace(/\bplease\b/gi, '')
    .trim()
  if (question) return { type: 'ask', question }

  return { type: 'none' }
}

function cleanCommandText(value: string) {
  return cleanString(value)
    .replace(/\b(?:thank you|thanks|please)\b[.!?]*$/i, '')
    .replace(/[.!?]+$/g, '')
    .trim()
}

function summarizeFallback(text: string) {
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (normalized.length <= 180) return normalized
  return `${normalized.slice(0, 177).trim()}...`
}

function extractActionLines(text: string) {
  const sentences = text
    .split(/(?<=[.!?])\s+|\n+/)
    .map((item) => item.trim())
    .filter(Boolean)
  return sentences
    .filter((sentence) => /\b(need to|should|todo|to do|will|follow up|send|build|create|finish|submit)\b/i.test(sentence))
    .map((sentence) => sentence.replace(/^(i|we)\s+/i, '').trim())
    .slice(0, 6)
}

function extractTopics(text: string) {
  const known = ['Presence', 'Even G2', 'G2', 'Obsidian', 'Agents with Memory', 'Hackathon', 'STT']
  const found = known.filter((topic) => text.toLowerCase().includes(topic.toLowerCase()))
  return unique(found.map((topic) => (topic === 'G2' ? 'Even G2' : topic)))
}

function getZonedParts(date: Date) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  const parts = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]))
  const hour = parts.hour === '24' ? '00' : parts.hour
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour,
    minute: parts.minute,
  }
}

function getBucketStartMinute(minute: string) {
  return String(Math.floor(Number(minute) / 5) * 5).padStart(2, '0')
}

function formatFiveMinuteLabel(bucketStartMinute: string) {
  const start = Number(bucketStartMinute)
  const end = Math.min(59, start + 4)
  return `${String(start).padStart(2, '0')}-${String(end).padStart(2, '0')}`
}

function fiveMinuteId(date: string, hour: string, bucketStartMinute: string) {
  return `5m-${date}-${hour}-${bucketStartMinute}`
}

function hourId(date: string, hour: string) {
  return `hour-${date}-${hour}`
}

function upsertMemoryEntry(entries: MemoryEntry[], nextEntry: MemoryEntry) {
  const index = entries.findIndex((entry) => entry.id === nextEntry.id)
  if (index === -1) {
    entries.push(nextEntry)
    return
  }
  entries[index] = nextEntry
}

function compareMemoryEntries(a: MemoryEntry, b: MemoryEntry) {
  return `${a.date}-${a.hour}-${a.minute}-${a.bucketStartMinute || ''}`.localeCompare(
    `${b.date}-${b.hour}-${b.minute}-${b.bucketStartMinute || ''}`,
  )
}

function compareTranscriptSegments(a: TranscriptSegment, b: TranscriptSegment) {
  return `${a.date}-${a.hour}-${a.minute}-${a.timestamp}`.localeCompare(`${b.date}-${b.hour}-${b.minute}-${b.timestamp}`)
}

function isDurableMemory(entry: MemoryEntry) {
  return entry.kind === 'hour' || entry.kind === 'five-minute' || entry.importance >= 0.5
}

const QUESTION_STOPWORDS = new Set([
  'what',
  'when',
  'where',
  'who',
  'why',
  'how',
  'did',
  'was',
  'were',
  'are',
  'the',
  'and',
  'for',
  'with',
  'that',
  'this',
  'you',
  'about',
  'tell',
  'show',
  'presence',
])

function indexPath() {
  return path.join(vaultPath, 'Index', 'memory-index.json')
}

function toLinkedList(prefix: 'People' | 'Topics', values: string[]) {
  return values.length ? values.map((value) => `- [[${prefix}/${safeFileName(value)}|${value}]]`) : ['- None']
}

function toPlainList(values: string[]) {
  return values.length ? values.map((value) => `- ${value}`) : ['- None']
}

function toTaskList(values: string[]) {
  return values.length ? values.map((value) => `- [ ] ${value}`) : ['- None']
}

function safeFileName(value: string) {
  return value.replace(/[\\/:*?"<>|#[\]]/g, '').replace(/\s+/g, ' ').trim().slice(0, 80)
}

function cleanList(value: unknown) {
  return Array.isArray(value) ? unique(value.map(cleanString).filter(Boolean)) : []
}

function cleanString(value: unknown) {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : ''
}

function decodeHtml(value: string) {
  return value
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'")
}

function tokenize(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((token) => token.length > 2)
}

function unique(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)))
}

function formatHud(value: string) {
  const lines = value
    .split(/\n+/)
    .map((line) => clipLine(line))
    .filter(Boolean)
    .slice(0, Number(process.env.HUD_MAX_LINES || 4))
  return lines.join('\n') || 'Presence\nlistening'
}

function clipLine(value: string) {
  const limit = Number(process.env.HUD_MAX_CHARS_PER_LINE || 32)
  const clean = value.replace(/\s+/g, ' ').trim()
  return clean.length > limit ? `${clean.slice(0, Math.max(0, limit - 3)).trim()}...` : clean
}

function clamp(value: number, min: number, max: number) {
  if (Number.isNaN(value)) return min
  return Math.max(min, Math.min(max, value))
}
