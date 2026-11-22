#!/bin/bash
# ClipForge launcher — double-click this file in Finder.
# Checks dependencies, installs what is missing, starts the engine and opens the app.
set -uo pipefail

cd "$(dirname "$0")" || exit 1
ROOT="$PWD"
PORT="${CLIPFORGE_PORT:-8756}"
URL="http://127.0.0.1:${PORT}"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

bold() { printf "\033[1m%s\033[0m\n" "$1"; }
info() { printf "  \033[36m·\033[0m %s\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗ %s\033[0m\n" "$1"; }

clear
bold "ClipForge — AI video & stream clipper"
echo

# ---------- already running? ----------
if curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
  ok "ClipForge is already running"
  open "$URL"
  echo
  echo "The app is open in your browser. Stop the existing server (Ctrl+C in its window) before starting another."
  exit 0
fi

# ---------- python ----------
bold "1/5 Python"
find_python() {
  for candidate in \
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
    /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
    python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver=$("$candidate" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null)
      if [ -n "${ver:-}" ] && [ "$ver" -ge 310 ]; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

if [ ! -x "$PY" ]; then
  SYS_PY="$(find_python)" || {
    fail "Python 3.10+ was not found."
    echo
    echo "  Install it once with:  brew install python@3.11"
    echo "  Then double-click START.command again."
    read -r -p "Press Enter to close…" _
    exit 1
  }
  info "creating virtual environment with $SYS_PY"
  "$SYS_PY" -m venv "$VENV" || { fail "could not create the virtual environment"; read -r -p "Press Enter…" _; exit 1; }
fi

# opencv must be 4.x: 5.x dropped the Haar cascade API used for face detection.
if ! "$PY" -c "import fastapi, uvicorn, faster_whisper, imageio_ffmpeg, cv2; assert cv2.__version__.split('.')[0] == '4'" >/dev/null 2>&1; then
  info "installing Python packages (first run only, a few minutes)"
  "$PY" -m pip install --upgrade pip -q
  if ! "$PY" -m pip install -q -r "$ROOT/backend/requirements.txt"; then
    fail "Python dependency install failed — see the output above"
    read -r -p "Press Enter to close…" _
    exit 1
  fi
fi
ok "Python environment ready"

# ---------- ffprobe ----------
bold "2/5 Media tools"
if ! command -v ffprobe >/dev/null 2>&1; then
  warn "ffprobe not found — probing media requires it."
  echo "     Install once with:  brew install ffmpeg"
  read -r -p "     Continue anyway? [y/N] " answer
  case "$answer" in [yY]*) ;; *) exit 1 ;; esac
else
  ok "ffprobe found"
fi
ok "encoder: $( "$PY" - <<'PY'
import sys
sys.path.insert(0, "backend")
from app.config import FFMPEG, ffmpeg_capabilities
caps = ffmpeg_capabilities()
hw = "VideoToolbox" if caps.get("encoders", {}).get("h264_videotoolbox") else "software"
subs = "libass" if caps.get("libass") else "no subtitles"
print(f"ffmpeg ({hw}, {subs})")
PY
)"

# ---------- fonts + speech model ----------
bold "3/5 Fonts & speech model"
FONT_COUNT=$(ls "$ROOT/assets/fonts"/*.ttf 2>/dev/null | wc -l | tr -d ' ')
if [ "${FONT_COUNT:-0}" -lt 20 ] || [ ! -d "$ROOT/data/models" ]; then
  info "downloading subtitle fonts and the Whisper base model (one time)"
  ( cd "$ROOT" && "$PY" backend/scripts/setup_assets.py all ) || warn "asset setup had problems — the app still runs"
else
  ok "$FONT_COUNT fonts · speech model cached"
fi

# ---------- frontend ----------
bold "4/5 Interface"
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  info "installing frontend packages"
  ( cd "$ROOT/frontend" && npm install --no-fund --no-audit ) || {
    fail "npm install failed"
    read -r -p "Press Enter to close…" _
    exit 1
  }
fi

needs_build=0
if [ ! -f "$ROOT/frontend/out/index.html" ]; then
  needs_build=1
elif [ -n "$(find "$ROOT/frontend/app" "$ROOT/frontend/components" "$ROOT/frontend/lib" \
      "$ROOT/frontend/package.json" "$ROOT/frontend/next.config.mjs" "$ROOT/frontend/tailwind.config.ts" \
      -newer "$ROOT/frontend/out/index.html" -print -quit 2>/dev/null)" ]; then
  needs_build=1
fi

if [ "$needs_build" = "1" ]; then
  info "building the interface"
  if ! ( cd "$ROOT/frontend" && npm run build >/tmp/clipforge-build.log 2>&1 ); then
    fail "frontend build failed — last lines:"
    tail -20 /tmp/clipforge-build.log
    read -r -p "Press Enter to close…" _
    exit 1
  fi
fi
ok "interface ready"

# ---------- start ----------
bold "5/5 Starting ClipForge"
(
  for _ in $(seq 1 90); do
    if curl -fsS --max-time 1 "$URL/api/health" >/dev/null 2>&1; then
      open "$URL"
      break
    fi
    sleep 1
  done
) &

echo
ok "engine:  $URL"
ok "app:     $URL  (opening in your browser)"
echo
echo "  Keep this window open while you use ClipForge. Press Ctrl+C to stop."
echo "  ────────────────────────────────────────────────────────────────"
echo
export CLIPFORGE_PORT="$PORT"
exec "$PY" -m uvicorn app.main:app --app-dir "$ROOT/backend" --host 127.0.0.1 --port "$PORT" --log-level info
