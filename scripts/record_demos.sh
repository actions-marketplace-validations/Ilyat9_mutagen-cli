#!/bin/bash
# Re-record every animated demo in assets/ with Charm VHS.
#
# Requires: vhs + ffmpeg (brew install vhs ffmpeg), and the project's dev
# environment (pip install -e ".[dev]") so `mutagen` and `pytest` resolve.
#
# Everything runs offline: before each tape, scripts/demo.py rebuilds the
# demo playground from the polygon fixture and pre-seeds its LLM cache from
# tests/fixtures/canned_mutants.json, so the recorded `mutagen run` replays
# from cache — no API key, no network. The playground is rebuilt before every
# tape because --invent-apply writes tests into the repo, which changes the
# test-to-function mapping and would stale the cache keys mid-session.

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
DEMO_DEST="${MUTAGEN_DEMO_DIR:-${TMPDIR:-/tmp}/mutagen-demo}"

# Make the dev environment's console scripts visible to the tape shell.
if [ -d .venv/bin ]; then
    export PATH="$PWD/.venv/bin:$PATH"
fi

for tape in assets/demo.tape assets/invent.tape; do
    echo "== building offline playground for $tape"
    DEMO_DIR="$("$PYTHON" scripts/demo.py --dest "$DEMO_DEST")"
    export MUTAGEN_DEMO_DIR="$DEMO_DIR"
    echo "== recording $tape"
    vhs "$tape"
done

ls -lh assets/*.gif
