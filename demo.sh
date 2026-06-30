#!/usr/bin/env bash
#
# demo.sh — run the whole portfolio loop end to end with one command.
#
# Walks the full system the way the "The System" diagram describes it:
#
#   notes-api  --(POST /notes)-->  note created (enrichment_status: pending)
#        |  BackgroundTask: POST /classify
#        v
#   classifier --(category/operational_domain)--> tags written back (status: done)
#        |
#        v
#   index.py   --(GET /notes)--> notes embedded into ChromaDB (kind="notes")
#        |
#        v
#   kb-agent   --(search_kb / search_notes)--> grounded answer with citations
#
# What it does, in order:
#   1. Starts the classifier service        (needs ANTHROPIC_API_KEY)
#   2. Starts notes-api with CLASSIFIER_URL set so enrichment fires
#   3. POSTs a sample note, then polls until enrichment_status flips pending -> done
#   4. Rebuilds the kb-agent vector index from the live notes
#   5. Asks the agent a question and prints the grounded answer (needs ANTHROPIC_API_KEY)
#
# The three repos are assumed to be siblings (the author's layout). Override any
# path or port with the env vars below if yours differ.
#
# Usage:
#   ANTHROPIC_API_KEY=sk-... ./demo.sh
#
set -euo pipefail

# --- Configuration (override via environment) --------------------------------
KB_AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLASSIFIER_DIR="${CLASSIFIER_DIR:-$(cd "$KB_AGENT_DIR/../defense-news-classifier" 2>/dev/null && pwd || true)}"
NOTES_API_DIR="${NOTES_API_DIR:-$(cd "$KB_AGENT_DIR/../notes-api" 2>/dev/null && pwd || true)}"

CLASSIFIER_PORT="${CLASSIFIER_PORT:-8000}"
NOTES_API_PORT="${NOTES_API_PORT:-8081}"
CLASSIFIER_URL="http://127.0.0.1:${CLASSIFIER_PORT}"
NOTES_API_URL="http://127.0.0.1:${NOTES_API_PORT}"

DEMO_QUESTION="${DEMO_QUESTION:-What do my notes say about the F-35 contract, and how is it classified?}"

# --- Preflight ---------------------------------------------------------------
fail() { echo "ERROR: $*" >&2; exit 1; }

[[ -n "${ANTHROPIC_API_KEY:-}" ]] || fail "ANTHROPIC_API_KEY is not set. The classifier and the agent both need it."
[[ -n "$CLASSIFIER_DIR" && -d "$CLASSIFIER_DIR" ]] || fail "classifier repo not found. Set CLASSIFIER_DIR to the defense-news-classifier checkout."
[[ -n "$NOTES_API_DIR" && -d "$NOTES_API_DIR" ]] || fail "notes-api repo not found. Set NOTES_API_DIR to the notes-api checkout."
command -v uv >/dev/null || fail "uv is not installed (https://docs.astral.sh/uv/)."
command -v curl >/dev/null || fail "curl is required."

# --- Process management ------------------------------------------------------
PIDS=()
cleanup() {
  echo
  echo "==> Shutting down services..."
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

wait_for_health() {
  # wait_for_health <url> <name>
  local url="$1" name="$2" i
  for i in $(seq 1 30); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "    $name is up."
      return 0
    fi
    sleep 1
  done
  fail "$name did not become healthy at $url within 30s. Check its logs above."
}

# --- 1. Classifier service ---------------------------------------------------
echo "==> [1/5] Starting classifier on :${CLASSIFIER_PORT} ..."
( cd "$CLASSIFIER_DIR" && uv run uvicorn api:app --app-dir src --port "$CLASSIFIER_PORT" ) &
PIDS+=("$!")
wait_for_health "${CLASSIFIER_URL}/health" "classifier"

# --- 2. notes-api (with enrichment wired to the classifier) ------------------
echo "==> [2/5] Starting notes-api on :${NOTES_API_PORT} (CLASSIFIER_URL=${CLASSIFIER_URL}) ..."
( cd "$NOTES_API_DIR" && CLASSIFIER_URL="$CLASSIFIER_URL" \
    uv run uvicorn notes_api.main:app --port "$NOTES_API_PORT" ) &
PIDS+=("$!")
wait_for_health "${NOTES_API_URL}/notes" "notes-api"

# --- 3. Create a note and watch enrichment land ------------------------------
echo "==> [3/5] Creating a sample note ..."
CREATE_BODY='{"title":"F-35 contract","content":"The Pentagon awarded a $4.2B contract for 24 F-35 fighters.","tags":["mine"]}'
NOTE_JSON="$(curl -fsS -X POST "${NOTES_API_URL}/notes" -H 'Content-Type: application/json' -d "$CREATE_BODY")"
NOTE_ID="$(printf '%s' "$NOTE_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
echo "    Created note id=${NOTE_ID} (status: pending)."

echo "    Polling for enrichment to complete ..."
STATUS=""
for i in $(seq 1 30); do
  NOTE_JSON="$(curl -fsS "${NOTES_API_URL}/notes/${NOTE_ID}")"
  STATUS="$(printf '%s' "$NOTE_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["enrichment_status"])')"
  if [[ "$STATUS" == "done" || "$STATUS" == "failed" ]]; then break; fi
  sleep 1
done
echo "    enrichment_status: ${STATUS}"
printf '    tags: '
printf '%s' "$NOTE_JSON" | python3 -c 'import sys,json; print(", ".join(json.load(sys.stdin)["tags"]))'
[[ "$STATUS" == "done" ]] || echo "    (warning: enrichment did not complete cleanly — the agent step may have less to work with)"

# --- 4. Rebuild the vector index from the live notes -------------------------
echo "==> [4/5] Rebuilding the kb-agent index from live notes ..."
( cd "$KB_AGENT_DIR" && uv run python scripts/index.py )

# --- 5. Ask the agent --------------------------------------------------------
echo "==> [5/5] Asking the agent:"
echo "    \"${DEMO_QUESTION}\""
echo "-------------------------------------------------------------------------"
( cd "$KB_AGENT_DIR" && uv run python -c "
from agent.agent import KBAgent
print(KBAgent().ask('''${DEMO_QUESTION}'''))
" )
echo "-------------------------------------------------------------------------"
echo "==> Done. Press Ctrl-C to stop the services (or they stop on exit)."
