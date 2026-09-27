# ⚡ AutoShorts AI — Autonomous Video Clipper & Multi-Platform Publisher

> **Transform long-form YouTube videos and podcasts into high-retention, viral vertical Shorts (9:16) with dynamic animated subtitles, face tracking, b-roll overlays, punch zooms, and automated multi-platform publishing.**

---

## 🌟 Key Features

* **🧠 AI Virality Scoring & Critique Gate:** Analyzes speech density, emotional triggers, and standalone narrative coherence to detect the top moments in any long-form video.
* **🎯 3 Smart Framing Modes:**
  * **Blurred 9:16:** Cinematic blurred canvas keeping the full original frame in focus.
  * **Smart Face Tracking:** OpenCV **YuNet** (`FaceDetectorYN`) face detection with smooth moving-average crop stabilization. Requires the ONNX model — see [Face tracking model](#-face-tracking-model).
  * **Split Gaming:** Top facecam + bottom gameplay stack specifically designed for stream recordings and Let's Plays.
* **🎨 13 Animated Subtitle Presets:** Hormozi Pop (Yellow/White), Beast (Electric Yellow), Kinetic Pop (TikTok style), Netflix Standard, BBC SDH Broadcast Box, Cinematic Auteur Serif, Cyber Green, Red Punch, Clean White, Karaoke, Boxed Clean, Minimal Caption, and Bold Pop (default).
* **🎬 Autonomous Video Polish:**
  * **Dynamic B-Roll Overlays:** Automatic keyword-triggered contextual stock footage.
  * **Emoji Popups:** Keyword-synced reaction emojis positioned above speaker dialogue.
  * **Snappy Silence Jump-Cuts:** Automatic silence detection removing dead air.
  * **1.25x Punch Zooms:** Pacing rhythm transitions on key speaker emphasis words.
  * **Profanity Bleeper:** 1000Hz censorship audio tone with automatic text asterisks.
  * **Branded Outro Cards:** Visual subscribe call-to-action cards.
* **🔥 Jev System One Viral Title & Tag Engine:**
  * Generates 5 distinct high-CTR viral candidate hooks across proven frameworks (Curiosity Gap, Pattern Interrupt/Warning, Spoken Climax Quote, Shock/Revelation, and Actionable Blueprint).
  * Automatically compiles high-density niche hashtags + extracted entity tags + `#shorts #viral #fyp`.
* **🚀 Multi-Platform Publishing:**
  * **Direct YouTube Shorts:** Official YouTube Data API v3 integration with 1-click loopback OAuth2 authentication.
  * **TikTok & Instagram Reels:** Multi-platform posting via Ayrshare API.
  * **1-Click Review Modal:** Pick from alternative hook chips, tweak description/tags, and publish directly from the web dashboard.

---

## 🛠️ System Prerequisites

1. **Python 3.10+**
2. **FFmpeg:** Ensure `ffmpeg` and `ffprobe` are installed and available on your system `PATH`.
   - *Windows (Chocolatey):* `choco install ffmpeg`
   - *Windows (Scoop):* `scoop install ffmpeg`
   - *macOS (Homebrew):* `brew install ffmpeg`
   - *Linux (Ubuntu/Debian):* `sudo apt-get install ffmpeg`

---

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/rayyanbobs-art/autonomous-clipper.git
cd autonomous-clipper
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

> **Note on `faster-whisper`:** this pulls in a large CTranslate2 runtime (~2 GB).
> It is only needed for word-level caption timing. If you are happy with
> cue-level subtitles, remove it from `requirements.txt` and supply an SRT/VTT
> transcript instead.

### 3. Face Tracking Model

`smart_face` framing needs the YuNet detector, which is **not** committed
(binary, ~340 KB). Download it once into `models/`:

```bash
mkdir -p models
curl -L -o models/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

The other two framing modes (`blurred`, `split_gaming`) work without it, but
`split_gaming` still calls the face tracker to place the facecam box.

### 4. Configure (optional)

Copy the environment template and fill in whatever you need:

```bash
cp .env.example .env
```

| Variable | Needed for |
|---|---|
| `JEV_API_KEY` | AI virality scoring and the title/tag engine |
| `PEXELS_API_KEY` | Automatic B-roll footage ([free key](https://www.pexels.com/api/)) |
| `GEMINI_API_KEY` | Niche idea blueprints (falls back to templates without it) |
| `AYRSHARE_API_KEY` | TikTok / Instagram Reels publishing |

YouTube credentials are **not** environment variables — see
[Social Accounts & Auto-Upload Setup](#-social-accounts--auto-upload-setup).

### 5. Launch the Web Studio
```bash
# On Windows, you can double-click Launch_Clipper.bat, or run:
python app.py
```
Open your browser to: **`http://127.0.0.1:5000`**

> The local server binds to `127.0.0.1` and has **no authentication**. Do not
> expose it to a network you do not control.

---

## 💻 Command Line (CLI) Usage

You can also run the clipper directly from your terminal:

```bash
python clipper.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --top 3 --candidates 15
```

### CLI Arguments:
| Argument | Default | Description |
|---|---|---|
| `--url` | *Required* | YouTube video URL to slice into Shorts |
| `--top` | `3` | Number of best Shorts to produce |
| `--candidates` | `15` | Number of candidate windows to score |
| `--style` | `bold_pop` | Subtitle preset (`hormozi`, `beast`, `kinetic_pop`, etc.) |
| `--framing` | `blurred` | Framing mode (`blurred`, `smart_face`, `split_gaming`) |
| `--no-broll` | `False` | Disable automatic B-roll video overlays |
| `--no-emojis` | `False` | Disable animated reaction emojis |
| `--no-snappy` | `False` | Disable silence jump-cuts |
| `--no-zooms` | `False` | Disable 1.25x punch zooms |
| `--no-outro` | `False` | Disable subscribe outro card |
| `--auto-bleep` | `False` | Enable 1000Hz audio censor bleep for profanity |

---

## 🔑 Social Accounts & Auto-Upload Setup

### Option A: YouTube Data API v3 (Direct & Free)
1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Create a new project and enable the **YouTube Data API v3**.
3. Under **Google Auth Platform** / **Clients**, click **+ Create Client** ➔ select **Desktop app**.
4. Download the credentials JSON and save it as **`client_secrets.json`** in the project root directory *(see `client_secrets.example.json`)*.
5. In the Web UI, open **⚙️ Settings** ➔ click **"Connect Channel"**.
6. Sign in with your Google account and grant permissions.

### Option B: Ayrshare API (TikTok & Instagram Reels)
1. Create an account at [ayrshare.com](https://www.ayrshare.com) and retrieve your API key.
2. In the Web UI, open **⚙️ Settings** ➔ paste your API key under **Multi-Platform Social API** ➔ click **Save All Settings**.

---

## 📁 Repository Structure

```
autonomous-clipper/
├── app.py                      # Flask Web Studio server & REST API
├── clipper.py                  # Core autonomous video clipping pipeline
├── title_tag_engine.py         # Jev viral title hook & hashtag engine
├── uploader.py                 # YouTube OAuth & Ayrshare publishing dispatcher
├── video_cutter.py             # FFmpeg filtergraph rendering engine
├── scorer.py                   # Speech transcript windowing & virality scoring
├── face_tracker.py             # OpenCV YuNet smart face tracking
├── subtitles.py                # Audio-synced timed text & ASS subtitle styles
├── broll_engine.py             # Contextual B-roll footage downloader
├── batch_rerender.py           # Batch re-rendering utility
├── niche_analyzer.py           # Outlier video and audience retention analyzer
├── niche_generator.py          # AI idea blueprint generator
├── niche_scraper.py            # YouTube transcript and channel scraper
├── templates/
│   └── index.html              # Modern Web Studio UI (Tailwind + Lucide)
├── models/                     # Face detector ONNX (git ignored, see setup)
├── output/                     # Rendered MP4 shorts & JSON metadata (git ignored)
├── temp/                       # Temporary audio/video working caches (git ignored)
├── .env.example                # Environment variable template
├── client_secrets.example.json # Google OAuth Desktop credentials template
├── upload_config.example.json  # Social upload configuration template
├── requirements.txt            # Python dependencies
└── Launch_Clipper.bat          # 1-click Windows desktop launcher
```

---

## 🧪 Running Tests

```bash
python -m unittest discover -p "test_*.py" -v
```

The suite is `unittest`-only (no `pytest` required) and takes roughly 2–3
minutes, most of it real FFmpeg work in `test_speed_clipper.py`.

> ⚠️ **Before running the full suite:** `test_title_and_uploader.py` contains a
> Flask endpoint test that writes to your real `upload_config.json` (it does not
> patch `UPLOAD_CONFIG_PATH` the way `test_05` does). Back up the file first, or
> run that module on its own and check your upload settings afterwards.
>
> `test_jev_features.py` and `test_niche_integration.py` make **live network
> calls** (the Jev API, YouTube). `test_jev_features.py` needs a paid
> `JEV_API_KEY` and will fail while that service is unavailable.

Which tests are worth trusting:

| File | Verdict |
|---|---|
| `test_subtitles_taxonomy.py` | Solid — pure functions, no I/O, mutation-verified |
| `test_jev_features.py` (tests 01, 02, 04, 05) | Solid — real calls, real thresholds |
| `test_title_and_uploader.py` (01–05) | Reasonable pure-function tests of the title engine |
| `test_confirmed_bugs_fixes.py` | **Mixed** — 02, 03 and 04 re-implement the logic they claim to cover and never call it; treat those three as unverified |
| `test_speed_clipper.py` | Real FFmpeg, but leaves ~94 MB in `temp/` each run and hardcodes a local path |

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).
