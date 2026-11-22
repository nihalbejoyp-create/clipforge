# ClipForge — local AI video & livestream clipper

ClipForge turns long videos and live streams into short, caption-burned, watermark-free clips.
Everything runs on your Mac: speech recognition, highlight detection, reframing, subtitle rendering
and encoding. No accounts, no paid APIs, no uploads to a third party.

---

## What it does

**Three modes, one engine**

| Mode | Input | Workflow |
| --- | --- | --- |
| Normal video clipping | MP4/MOV/MKV/WebM upload, local file, or a supported URL (YouTube, Twitch VODs and the ~1000 sites yt-dlp supports) | Whisper transcribes → moments ranked → clips cut, captioned, reframed, exported |
| Recorded stream clipping | Uploaded stream recording | Same pipeline, with facecam/gameplay layouts and automatic facecam detection as the default |
| Live stream clipping | HLS (`.m3u8`), RTMP, YouTube/Twitch live URLs | Continuous capture into 2 s segments → rolling audio + speech analysis → clips cut **while the stream is still live**, with automatic reconnection |

**Per-job customization (identical in all three modes)**

- 1, 2, 5, 10, 20, 50, 100 or a custom number of clips
- 15/30/45/60/90/120 s or a custom clip length
- 720p / 1080p / 1440p / 4K — automatically capped to what the source can actually resolve (never fake 4K)
- Original / high (EBU R128 normalised) / custom-bitrate audio, stereo preserved, A/V locked
- MP4 (H.264), MP4 (H.265), MOV
- 9:16, 16:9, 1:1, 4:5, facecam-top, facecam-bottom, facecam-only, gameplay-only, automatic smart reframe
- Six caption presets, 16 fonts, per-word highlighting, karaoke fill, fade or static, size/weight/colour/outline/shadow/box/position/emoji
- Highlight sensitivity, overlap control, Whisper model size, language

**Clip editor** — trim in/out with the player, swap font/style/layout/resolution/audio, re-render that
single clip, pick a different moment, download it. "Render all" and a zip of the whole project.

**Live dashboard** — live HLS preview of what is being captured, connection status, elapsed time,
resolution, audio state, disk space, highlights detected, clips rendering/completed/failed, and a clip
gallery that fills in by itself. Capture reconnects automatically, survives app restarts, and never
deletes captured footage unless you delete the session.

---

## Requirements

- macOS on Apple Silicon (tested on an M1 Pro, 16 GB, macOS 26/27). Intel Macs work too, slower.
- Python 3.10+ (`brew install python@3.11` if you don't have one)
- Node.js 18+ (`brew install node`)
- `ffprobe` on the PATH (`brew install ffmpeg`). The encoder binary is bundled with the app
  (`imageio-ffmpeg`, an FFmpeg 9 build with libass + VideoToolbox), so subtitle burn-in and hardware
  encoding work even if your Homebrew FFmpeg was compiled without libass.
- ~2 GB of free disk for the speech model, fonts and dependencies; more for the videos you clip.

---

## Install & launch

0. Clone the repository and enter it: `git clone <this-repo-url> && cd ClipForge`
1. Double-click **`START.command`**.

That's it. On first run the launcher:

1. finds a suitable Python, creates `.venv/`, installs the backend packages;
2. verifies `ffprobe` and reports the encoder (VideoToolbox / libass);
3. downloads the subtitle fonts (16 families), the face detector, and the Whisper `base` model once;
4. installs the frontend packages and builds the interface;
5. starts the engine on `http://127.0.0.1:8756` and opens it in your browser.

Later launches skip everything that is already done and start in a couple of seconds.
Keep the Terminal window open while you use the app; `Ctrl+C` stops it.

If macOS refuses to open the file the first time: right-click → **Open**, or
`chmod +x START.command` once in Terminal.

### Manual start (development)

```bash
# backend
python3.11 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8756

# frontend (hot reload) — proxy the API by using the backend for data
cd frontend && npm install && npm run dev     # http://localhost:3311
# production build served by FastAPI at :8756
npm run build
```

Environment variables: `CLIPFORGE_PORT` (default 8756), `CLIPFORGE_DATA` (data directory),
`CLIPFORGE_FFMPEG` / `CLIPFORGE_FFPROBE` (override binaries), `CLIPFORGE_DEBUG=1` (log every ffmpeg command).

---

## How it works

```
input (file / URL / live)
   │
   ├─ ingest ──────────────► data/projects/<id>/source.*         (never auto-deleted)
   │
   ├─ audio 16 kHz mono ───► faster-whisper (local, word timestamps)
   │        └─ RMS energy profile (0.5 s blocks) ─┐
   │                                               ▼
   ├─ candidate windows from sentence boundaries ─ scoring ─ NMS + de-duplication
   │     energy rank · energy peak · dynamics · speech rate · hook words · completeness · length fit
   │
   ├─ clip editor overrides (trim, style, layout, audio)
   │
   └─ ffmpeg: layout filter graph + libass subtitles + loudnorm + VideoToolbox
        └─ data/projects/<id>/clips/NN_title_id.mp4 (+ thumbnail)
```

**Live path**

```
HLS / RTMP URL ─► ffmpeg capture (H.264, 2 s keyframe-aligned segments) ─► data/live/<id>/segments
                     │                                          │
                     │                                          └─► /api/live/<id>/playlist.m3u8 (live preview)
                     ├─ per-segment RMS energy
                     ├─ every ~20 s: transcribe the new chunk (word timestamps, absolute offsets)
                     └─ score last N s → highlight → wait for post-roll → remux covering segments
                          → render with the exact same subtitle/layout engine → clip gallery
```

Highlights are scored, not sampled: quiet, low-information stretches never become clips, and clips do
not repeat each other unless you enable overlapping moments.

---

## Storage

```
data/clipforge.sqlite3                  projects, clips, jobs, live sessions
data/projects/<id>/source.mp4           imported footage (kept until you delete the project)
data/projects/<id>/transcript.json      words + segments + language
data/projects/<id>/clips/*.mp4          rendered clips, watermarks never added
data/live/<id>/segments/*.ts            captured livestream segments (the archive)
data/live/<id>/segments.json            segment index, survives restarts
data/models/                            local Whisper models
assets/fonts/                           subtitle fonts (fetched at install)
```

Deleting a project from the UI removes its clips, transcript and working copy. Nothing is removed in
the background, and the app refuses to render when free disk drops below ~1 GB (5 GB for live capture).

---

## API

`GET /api/system` · `POST /api/validate-url` · `POST /api/upload`
`POST/GET /api/projects` · `GET/PATCH/DELETE /api/projects/{id}` · `POST /api/projects/{id}/analyze`
`GET /api/projects/{id}/clips|jobs|transcript|thumb|source` · `POST /api/projects/{id}/export-all`
`GET /api/projects/{id}/download-all` · `GET /api/clips` · `GET/PATCH /api/clips/{id}`
`POST /api/clips/{id}/render|regenerate` · `GET /api/clips/{id}/video|thumb|download`
`POST /api/preview/subtitle` · `GET /api/jobs/{id}` · `POST /api/jobs/{id}/cancel`
`GET/POST /api/live` · `GET /api/live/{id}` · `POST /api/live/{id}/stop`
`GET /api/live/{id}/playlist.m3u8` · `GET /api/live/{id}/segments/{name}`

Interactive docs: `http://127.0.0.1:8756/docs`.

---

## Honest limitations

- **Live sources**: pages are resolved with yt-dlp, which can break when a platform changes. DRM,
  paywalled and login-gated streams are not supported — don't try to bypass them. HLS/RTMP sources you
  are authorised to read work directly.
- **Live latency**: a clip needs its post-roll captured before it can be cut, so expect roughly
  `clip duration × (1 − pre-roll) + a few seconds` between the moment and the finished file.
- **Face detection** uses a Haar cascade (frontal faces). Automatic facecam detection is a quadrant
  vote with a 3× head-size crop; if it guesses wrong, set the facecam region manually in the layout panel.
- **4K** only happens when the source actually carries it. 1080p sources render 1080p-tier canvases;
  a 4K request is capped instead of upscaled.
- **Speech recognition** runs on CPU (int8). `base` is the default speed/quality point; `medium` is
  noticeably more accurate and several times slower.
- **Highlight detection** is a transparent feature model (energy, pacing, hooks, completeness), not a
  neural scorer. It is deterministic and fast, and sensitivity controls how eager it is.
- This is a single-user local application: no authentication, and it binds to `127.0.0.1` only.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ffprobe not found` | `brew install ffmpeg` (only ffprobe is needed from Homebrew) |
| Port 8756 busy | `CLIPFORGE_PORT=8757 ./START.command` |
| "subtitles unavailable" in Settings | Reinstall requirements: `.venv/bin/pip install -r backend/requirements.txt` (bundled FFmpeg has libass) |
| First transcription is slow | The Whisper model downloads once into `data/models`; the launcher pre-fetches it |
| Live clip never appears | Watch the session stats — energy and transcript counters must grow. Very quiet streams with a high sensitivity threshold legitimately produce nothing |
| Build errors after pulling changes | `rm -rf frontend/.next frontend/out && ./START.command` |

---

## License

MIT — see [LICENSE](LICENSE).

Nothing downloaded at install time is bundled in this repository: the 16 subtitle fonts are fetched
from Google Fonts (each under its own family licence), the face detector comes from OpenCV, and the
Whisper `base` model is pulled by `faster-whisper` on first run. Your own footage, clips, database and
YouTube tokens live in `data/`, which is git-ignored and never leaves your machine.
