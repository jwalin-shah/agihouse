#!/usr/bin/env bash
# inbox batch runner — orchestrates claude -p workers for bulk inbox ops
# Usage: ./batch/batch-runner.sh --mode archive [OPTIONS]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCHIVE_INPUT="$SCRIPT_DIR/archive-input.tsv"
ARCHIVE_STATE="$SCRIPT_DIR/archive-state.tsv"
LOGS_DIR="$SCRIPT_DIR/logs"
SERVER_URL="${INBOX_SERVER_URL:-http://localhost:9849}"
SERVER_TOKEN="${INBOX_SERVER_TOKEN:-}"

# Defaults
MODE="archive"
PARALLEL=1
DRY_RUN=false
RETRY_FAILED=false
MAX_RETRIES=3

usage() {
  cat <<'USAGE'
inbox batch runner

Usage: batch-runner.sh --mode archive [OPTIONS]

Options:
  --mode MODE         Operation mode: archive (default)
  --parallel N        Parallel workers (default: 1)
  --dry-run           List pending items, don't execute
  --retry-failed      Also retry failed items
  --max-retries N     Max retries per item (default: 3)
  -h, --help          Show this help

Files:
  archive-input.tsv   Input threads (thread_id, source, notes)
  archive-state.tsv   Progress state (auto-managed)
  logs/               Per-item logs
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --parallel) PARALLEL="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --retry-failed) RETRY_FAILED=true; shift ;;
    --max-retries) MAX_RETRIES="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

mkdir -p "$LOGS_DIR"

# Initialize state file if missing
if [[ ! -f "$ARCHIVE_STATE" ]]; then
  echo -e "thread_id\tsource\tstatus\tarchived_at\terror\tretries" > "$ARCHIVE_STATE"
fi

# Build auth header arg
AUTH_HEADER=""
if [[ -n "$SERVER_TOKEN" ]]; then
  AUTH_HEADER="-H \"Authorization: Bearer $SERVER_TOKEN\""
fi

archive_thread() {
  local thread_id="$1"
  local source="$2"
  local log_file="$LOGS_DIR/${source}-${thread_id}.log"

  echo "[$(date -u +%FT%TZ)] archiving $source/$thread_id" >> "$log_file"

  if [[ "$source" == "gmail" ]]; then
    response=$(curl -s -w "\n%{http_code}" \
      -X POST "$SERVER_URL/gmail/batch-modify" \
      -H "Content-Type: application/json" \
      ${SERVER_TOKEN:+-H "Authorization: Bearer $SERVER_TOKEN"} \
      -d "{\"msg_ids\": [\"$thread_id\"], \"add_label_ids\": [], \"remove_label_ids\": [\"INBOX\"]}" \
      2>> "$log_file")
    http_code=$(echo "$response" | tail -1)
    if [[ "$http_code" == "200" ]]; then
      echo "ok"
    else
      echo "error:$http_code"
    fi
  else
    echo "error:unsupported source $source"
  fi
}

update_state() {
  local thread_id="$1" source="$2" status="$3" ts="$4" error="$5" retries="$6"
  local tmpfile
  tmpfile=$(mktemp)
  awk -v id="$thread_id" -v src="$source" -v st="$status" -v ts="$ts" \
      -v err="$error" -v ret="$retries" 'BEGIN{OFS="\t"}
    NR==1 {print; next}
    $1==id && $2==src {print id,src,st,ts,err,ret; next}
    {print}' "$ARCHIVE_STATE" > "$tmpfile"
  mv "$tmpfile" "$ARCHIVE_STATE"
}

# Sync input into state (add missing rows as pending)
tail -n +2 "$ARCHIVE_INPUT" | while IFS=$'\t' read -r thread_id source notes; do
  [[ -z "$thread_id" ]] && continue
  if ! awk -v id="$thread_id" -v src="$source" 'NR>1 && $1==id && $2==src {found=1} END{exit !found}' "$ARCHIVE_STATE" 2>/dev/null; then
    echo -e "$thread_id\t$source\tpending\t\t\t0" >> "$ARCHIVE_STATE"
  fi
done

# Collect pending items
mapfile -t PENDING < <(
  awk -v retry="$RETRY_FAILED" -v max="$MAX_RETRIES" 'BEGIN{OFS="\t"}
    NR==1 {next}
    $3=="pending" {print $1,$2; next}
    retry=="true" && $3=="failed" && $6+0 < max+0 {print $1,$2}' \
    "$ARCHIVE_STATE"
)

if [[ ${#PENDING[@]} -eq 0 ]]; then
  echo "No pending items."
  exit 0
fi

echo "Pending: ${#PENDING[@]} threads (mode=$MODE, parallel=$PARALLEL, dry-run=$DRY_RUN)"

if [[ "$DRY_RUN" == "true" ]]; then
  for item in "${PENDING[@]}"; do
    echo "  would archive: $item"
  done
  exit 0
fi

# Process with optional parallelism
active=0
for item in "${PENDING[@]}"; do
  thread_id=$(echo "$item" | cut -f1)
  source=$(echo "$item" | cut -f2)

  (
    result=$(archive_thread "$thread_id" "$source")
    ts=$(date -u +%FT%TZ)
    retries=$(awk -v id="$thread_id" -v src="$source" 'NR>1 && $1==id && $2==src {print $6+1}' "$ARCHIVE_STATE")
    if [[ "$result" == "ok" ]]; then
      update_state "$thread_id" "$source" "completed" "$ts" "" "$retries"
      echo "  ✓ $source/$thread_id"
    else
      update_state "$thread_id" "$source" "failed" "$ts" "$result" "$retries"
      echo "  ✗ $source/$thread_id: $result"
    fi
  ) &

  active=$((active + 1))
  if [[ $active -ge $PARALLEL ]]; then
    wait -n 2>/dev/null || wait
    active=$((active - 1))
  fi
done
wait

echo ""
completed=$(awk 'NR>1 && $3=="completed"' "$ARCHIVE_STATE" | wc -l | tr -d ' ')
failed=$(awk 'NR>1 && $3=="failed"' "$ARCHIVE_STATE" | wc -l | tr -d ' ')
echo "Done. Archived: $completed  Failed: $failed"
