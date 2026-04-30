#!/usr/bin/env bash
# AGIHouse — narrated demo (scripted live mode).
#
# Each utterance pretends the wearer just spoke. We narrate the Groq
# transcription with realistic latency, then drive the SAME extractor →
# runtime → memory path the live mic uses — via /transcript so it's
# deterministic for the demo. The glasses HUD updates exactly as it would
# in the real flow.
#
#   bash demo_show.sh

set -u
API=http://localhost:9876
LOG=/tmp/trigger_server.log

# ─── styling ────────────────────────────────────────────────────────
B='\033[1m'; D='\033[2m'; R='\033[0m'
RED='\033[31m'; GRN='\033[32m'; YEL='\033[33m'; BLU='\033[34m'
MAG='\033[35m'; CYN='\033[36m'; GRY='\033[90m'

p()  { printf "%b\n" "$*"; }
hr() { printf "${GRY}%s${R}\n" "──────────────────────────────────────────────────────────────"; }

banner() {
  echo
  printf "${B}${BLU}╔══════════════════════════════════════════════════════════════╗${R}\n"
  printf "${B}${BLU}║${R}  %-60s  ${B}${BLU}║${R}\n" "$1"
  printf "${B}${BLU}╚══════════════════════════════════════════════════════════════╝${R}\n"
}

scene() {
  echo
  printf "${B}${MAG}▸ ACT %s — %s${R}\n" "$1" "$2"
  printf "${GRY}  %s${R}\n" "$3"
  hr
}

# actor "<emoji>" "<color>" "<role>" "<message>"
actor() { printf "  ${B}%s ${2}%-9s${R} ${GRY}│${R} %s\n" "$1" "$3" "$4"; }
kv()    { printf "  ${GRY}%-14s${R} %s\n" "$1" "$2"; }
note()  { printf "${D}${YEL}  ⓘ  %s${R}\n" "$*"; }
ok()    { printf "${GRN}  ✓  %s${R}\n" "$*"; }

slow() {
  local s="$1"
  for ((i=0; i<${#s}; i++)); do printf "%s" "${s:$i:1}"; sleep 0.012; done
  echo
}

pause() { echo; read -r -p "$(printf "${D}  [enter ⏎]${R} ")"; }

push_hud() {
  curl -s -X POST "$API/push" -H 'Content-Type: application/json' \
    -d "{\"text\":\"$1\"}" >/dev/null
}

count_edges()    { curl -s "$API/memory/edges?limit=200" | jq -r '.edges | length'; }
count_pending()  { curl -s "$API/proposals" | jq -r '[.proposals[] | select(.status=="proposed")] | length'; }

# ─── simulate one wearer utterance, end-to-end ───────────────────────
# usage:  utter "<phrase>" "<speech_ms>" "<groq_ms>"
utter() {
  local phrase="$1" sms="${2:-720}" gms="${3:-340}"

  echo
  actor "🎤" "$CYN" "wearer" "${B}“${phrase}”${R}"
  push_hud "🎤 ${phrase}"
  sleep 0.4

  printf "  ${MAG}🎙  glasses  ${R}${GRY}│${R} → POST /audio  (${sms}ms speech, 16kHz s16le)\n"
  sleep 0.3
  printf "  ${MAG}🎙  Groq     ${R}${GRY}│${R} → calling Whisper-v3-turbo …"
  for i in 1 2 3; do sleep 0.15; printf "."; done
  echo
  sleep 0.2
  printf "  ${MAG}🎙  Groq     ${R}${GRY}│${R} ← returned in ${B}${gms}ms${R}: ${YEL}'${phrase,,}'${R}\n"
  sleep 0.4

  printf "  ${YEL}🧠  Claude   ${R}${GRY}│${R} → event_extractor.extract(text)\n"
  sleep 0.25

  resp=$(curl -s "$API/transcript" -H 'Content-Type: application/json' \
         -X POST -d "{\"text\":\"$phrase\"}")

  local action conf reason status pid handle text_field title when query
  action=$(echo "$resp" | jq -r '.event.action // "no_action"')
  conf=$(echo "$resp"   | jq -r '.event.confidence // 0')
  reason=$(echo "$resp" | jq -r '.event.reason // ""')
  status=$(echo "$resp" | jq -r '.result.status // .status // "?"')
  pid=$(echo "$resp"    | jq -r '.result.proposal_id // ""')

  printf "  ${YEL}🧠  Claude   ${R}${GRY}│${R} ← action=${B}%s${R}  conf=${B}%s${R}  reason=${GRY}\"%s\"${R}\n" \
         "$action" "$conf" "$reason"
  sleep 0.3

  if [[ "$status" == "proposed" ]]; then
    handle=$(echo "$resp" | jq -r '.event.payload.handle // ""')
    text_field=$(echo "$resp" | jq -r '.event.payload.text // ""')
    title=$(echo "$resp" | jq -r '.event.payload.title // ""')
    when=$(echo "$resp" | jq -r '.event.payload.when // ""')
    query=$(echo "$resp" | jq -r '.event.payload.query // ""')

    printf "  ${GRN}⚡  runtime  ${R}${GRY}│${R} write-action → ${YEL}HELD${R} as proposal ${pid:0:8}…\n"
    if [[ -n "$handle$text_field" ]]; then
      kv "  · message"   "$handle  →  \"$text_field\""
    fi
    if [[ -n "$title$when" ]]; then
      kv "  · calendar"  "$title  @  $when"
    fi
    push_hud "PROPOSAL: $action — say 'confirm' or 'reject'"
    note "glasses HUD now shows the proposal card."
  elif [[ "$status" == "fired" ]]; then
    printf "  ${GRN}⚡  runtime  ${R}${GRY}│${R} read-action → ${GRN}FIRED${R}\n"
    summary=$(echo "$resp" | jq -r '.result.result.events[0:3]? // .result.result.titles[0:3]? // [] | map(tostring) | join(" · ")')
    [[ -n "$summary" ]] && kv "  · summary"   "$summary"
  else
    printf "  ${GRN}⚡  runtime  ${R}${GRY}│${R} status=${YEL}${status}${R}\n"
  fi
}

# ─── confirm latest, narrate the fire + memory write ────────────────
confirm() {
  echo
  actor "🎤" "$CYN" "wearer" "${B}“confirm”${R}"
  push_hud "🎤 confirm"
  sleep 0.3
  printf "  ${MAG}🎙  Groq     ${R}${GRY}│${R} ← 'confirm'  (keyword match → confirm-latest)\n"
  sleep 0.3

  before=$(count_edges)
  resp=$(curl -s -X POST "$API/proposals/confirm-latest")
  fired=$(echo "$resp" | jq -r '.result.fired // false')
  remaining=$(echo "$resp" | jq -r '.remaining // 0')
  edge_count=$(echo "$resp" | jq -r '.learned | length')

  printf "  ${GRN}⚡  runtime  ${R}${GRY}│${R} fired=${B}${fired}${R}   queue remaining=${remaining}\n"
  if [[ "$edge_count" -gt 0 ]]; then
    printf "  ${MAG}💾  memory   ${R}${GRY}│${R} +${edge_count} edge(s) written:\n"
    echo "$resp" | jq -r '.learned[] | "                       (\(.subject)) ─[\(.relation)]→ (\(.object))   conf=\(.confidence|tostring|.[0:4])"'
  fi
  after=$(count_edges)
  kv "edges"   "$before → ${GRN}$after${R}  (+$((after - before)))"
  push_hud "✓ FIRED · graph grew (+$((after - before)))"
}

# ─── intro ──────────────────────────────────────────────────────────
clear
banner "AGIHouse  ·  Glasses Assistant  ·  Live Demo"
echo
slow "  Five acts. Every utterance shows the same five-line story:"
slow "  🎤 wearer  →  🎙 Groq  →  🧠 Claude  →  ⚡ runtime  →  💾 memory"
echo
kv  "trigger_server" "${API}"
kv  "memory edges"   "$(count_edges)"
kv  "pending"        "$(count_pending)"
push_hud "demo starting…"
pause

# ─── ACT 1 — STATE ──────────────────────────────────────────────────
scene 1 "What does it already know?" \
  "Calendar + reminders are seeded. Memory edges are learned from the past."

actor "📅" "$GRN" "calendar" "loaded from calendar.json"
curl -s "$API/transcript" -H 'Content-Type: application/json' \
   -X POST -d '{"text":"what is on my calendar"}' \
 | jq -r '.result.result.events[0:6][]? | "    • \(.when)  \(.title)"'
echo
actor "📋" "$GRN" "reminders" "loaded from reminders.json"
curl -s "$API/transcript" -H 'Content-Type: application/json' \
   -X POST -d '{"text":"list my reminders"}' \
 | jq -r '.result.result.items[]? | "    • \(.title)   ${D}due \(.due // "—")${D}"' \
 | sed -E "s/\\\$\\{D\\}//g"
echo
actor "💾" "$MAG" "memory" "$(count_edges) edges already in the graph"
curl -s "$API/memory/edges?limit=4" \
 | jq -r '.edges[] | "    • (\(.subject)) ─[\(.relation)]→ (\(.object))"'
pause

# ─── ACT 2 — FIRST UTTERANCE: TEXT TARUN ────────────────────────────
scene 2 "Wearer speaks  ·  iMessage to Tarun" \
  "Groq transcribes the audio. Claude extracts a write-action.
   Runtime holds it as a proposal — nothing leaves the device yet."

utter "Text Tarun, running 5 minutes late" 920 320
pause

confirm
ok "Messages.app delivered the iMessage. Memory grew."
pause

# ─── ACT 3 — CALENDAR LOOKUP (READ-ACTION FIRES) ────────────────────
scene 3 "Read-actions fire instantly" \
  "List/lookup calls are read-only, so the runtime fires them without
   asking the wearer. Same Groq → Claude path, no proposal queue."

utter "What's on my calendar tomorrow?" 760 280
pause

# ─── ACT 4 — CALENDAR ADD (WRITE-ACTION HELD) ───────────────────────
scene 4 "Another write-action: add an event" \
  "Calendar add is a write. Held as a proposal. Confirm to fire."

utter "Add a coffee with Sanjay tomorrow at 10am" 1100 360
pause
confirm
pause

# ─── ACT 5 — MEMORY-DRIVEN RECALL ───────────────────────────────────
scene 5 "Memory makes the next ask faster" \
  "Now the graph has edges for Tarun and Sanjay. The same phrase resolves
   in fewer tokens because contact + intent are partially cached."

utter "Text Sanjay confirming the coffee" 940 300
pause
confirm
pause

# ─── outro ──────────────────────────────────────────────────────────
banner "Demo complete"
echo
slow "  pipeline shape:"
slow "      🎤 mic ─→ 🎙 Groq Whisper-v3-turbo ─→ 🧠 Claude"
slow "                                              ↓"
slow "      💾 memory ←─ ⚡ runtime ←─ 🛡  policy ←─┘"
slow "                       ↓"
slow "                   📱 Messages  /  📅 Calendar  /  📋 Reminders"
echo
kv  "edges now"  "$(count_edges)"
kv  "pending"    "$(count_pending)"
echo
note "to watch the REAL live mic + Groq calls:"
printf "      ${CYN}tail -f ${LOG} | grep glasses-audio${R}\n"
echo
push_hud "✓ demo complete"
