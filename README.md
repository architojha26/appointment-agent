# 🎙️ Kavita — AI Voice Appointment Agent

A web-based AI voice agent with an animated avatar that conducts natural phone-style conversations to book, retrieve, cancel, and modify appointments in real time.

> `python main.py` → open `http://localhost:8765` → click **Start Conversation**

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![OpenAI](https://img.shields.io/badge/LLM-GPT--4o--mini-green) ![Azure](https://img.shields.io/badge/STT-Azure%20Speech-0078D4) ![Cartesia](https://img.shields.io/badge/TTS-Cartesia-purple)

---

## ✨ What It Does

- **Listens** to what you say via Azure Speech-to-Text (real-time streaming)
- **Thinks** using OpenAI GPT-4o-mini with function-calling (8 appointment tools)
- **Speaks** back naturally via Cartesia TTS (WebSocket streaming)
- **Shows** an animated SVG avatar with real-time lip-sync, eye blinks, and breathing
- **Displays** every tool call live on the web UI (slot grids, appointment cards, user profiles)
- **Summarizes** the entire conversation at the end and shows it as an overlay

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                            main.py                                  │
│       Creates IPC objects, spawns processes, runs event loop        │
└─────────┬────────────────────┬─────────────────────┬────────────────┘
          │                    │                     │
          ▼                    ▼                     ▼
┌─────────────────┐  ┌──────────────────────┐  ┌───────────────────┐
│ Speaker Process  │  │ Conversation Manager │  │   Avatar Server   │
│ (multiprocessing)│  │    (async task)      │  │    (aiohttp)      │
│                  │  │                      │  │                   │
│ • CartesiaTTS    │  │ • MicStream capture  │  │ • Serves HTML UI  │
│ • PyAudio play   │  │ • Azure STT (push)   │  │ • WebSocket push  │
│ • RMS energy     │  │ • LLM orchestration  │  │ • Start button    │
│   measurement    │  │ • Tool dispatch      │  │ • Event broadcast │
└────────┬─────────┘  └──────────┬───────────┘  └────────┬──────────┘
         │                       │                       │
         │         mp.Manager() Queues                   │
         │  ┌──────────────────────────┐                 │
         ├──│ mp_commands_queue        │  (speak / terminate)
         │  ├──────────────────────────┤
         ├──│ agent_status_queue       │  (speaking / done / interrupted)
         │  ├──────────────────────────┤
         │  │ avatar_queue             │──────────────────► Browser
         │  └──────────────────────────┘                  (WebSocket)
         ▼
   ┌───────────┐    ┌──────────────┐    ┌──────────────────┐
   │ Cartesia  │───►│ PyAudio      │    │ data/            │
   │ TTS API   │    │ Speaker      │    │ appointments.json│
   └───────────┘    └──────────────┘    └──────────────────┘
```

### Data Flow (single utterance)

```
[Microphone]
    │  audio chunks (16kHz, 16-bit, mono)
    ▼
[MicStream] ──► raw PCM via run_in_executor (non-blocking)
    │
    ▼
[SimpleAzureSTT] ──► push_stream.write(chunk)
    │                  Azure processes in cloud
    │                  returns partial → final transcriptions
    ▼
[Conversation Manager] ──► collects partials, waits 2s silence
    │                       joins fragments into full utterance
    ▼
[ConversationalLLM] ──► OpenAI chat.completions with tool definitions
    │                    LLM may call tools (identify_user, fetch_slots, etc.)
    │                    tool results fed back → LLM generates final text
    ▼
[mp_commands_queue] ──► {"action": "speak", "text": "...", "turn_id": N}
    │
    ▼
[Speaker Process]
    │  CartesiaTTS.stream(text) → yields audio chunks
    │  each chunk: measure RMS → send audio_energy to avatar_queue
    │  PyAudio stream.write(chunk) → plays through speakers
    │  agent_status_queue.put({"action": "done_speaking"})
    ▼
[Avatar Server] ◄── avatar_queue events
    │  broadcasts via WebSocket to all connected browsers
    ▼
[Browser — index.html]
    • mouth opens/closes based on audio_energy
    • transcript updates with agent/user text
    • tool call cards render with structured results
```

---

## 📂 Project Structure

```
voice_stack/
├── main.py                          # Entry point — process orchestration
├── mic_stream.py                    # Microphone capture (sounddevice)
│
├── core/
│   ├── conversation_manager.py      # Main async orchestrator
│   ├── llm_handler.py               # OpenAI LLM with function-calling
│   └── speaker.py                   # TTS + playback (separate process)
│
├── stt/
│   └── azure_stt.py                 # Azure Speech-to-Text (push stream)
│
├── tts/
│   └── cartesia_tts.py              # Cartesia TTS (WebSocket streaming)
│
├── services/
│   ├── appointment_handler.py       # 8 tool functions + OpenAI tool schemas
│   └── conversation_summarizer.py   # Post-call LLM summary generation
│
├── avatar/
│   ├── server.py                    # aiohttp WebSocket server
│   └── index.html                   # Avatar UI (SVG + tool cards + transcript)
│
├── utils/
│   ├── logger.py                    # Custom logger
│   ├── conversation_logger.py       # Per-call turn logging
│   └── prompts.py                   # agent prompts
│
└── data/
    └── appointments.json            # Appointment database (auto-created with seed data)
```

---

## 🚀 Quick Start

### 1. Create & activate virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Create your `.env` file

> ⚠️ **No `.env.example` is included.** You must create the `.env` file yourself in the project root.

```bash
touch .env
```

Open `.env` in any editor and paste:

```env
# ─── REQUIRED: Speech-to-Text (Azure) ─────────────────────
AZURE_SPEECH_KEY=your_azure_speech_key_here
AZURE_SPEECH_REGION=southeastasia

# ─── REQUIRED: LLM (OpenAI) ───────────────────────────────
OPENAI_API_KEY=your_openai_api_key_here

# ─── REQUIRED: Text-to-Speech (Cartesia) ──────────────────
CARTESIA_API_KEY=your_cartesia_api_key_here

# ─── OPTIONAL ──────────────────────────────────────────────
AGENT_NAME=kavita                   # Avatar display name
OPENAI_MODEL=gpt-4o-mini           # LLM model to use
AVATAR_PORT=8765                    # Web UI port
```

Replace the placeholder values with your actual API keys (see section below for where to get them).

### 3. Run

```bash
python main.py
```

You will see:

```
[main] 🚀 Appointment Booking Agent started (call_id=abc12345)
[main] 🔊 Speaker PID: 12345
[main] 🎭 Avatar: http://localhost:8765
[main] 🌐 Open the URL and click 'Start Conversation' to begin
```

Open **http://localhost:8765** in your browser → click **Start Conversation** → speak.
**Note:** Try to use earphones, since its mic stream, in case of speaker, agent voice will be sent back as the user.

---

## 🔑 Where to Get API Keys

| Service | What For | Free Tier | Sign Up |
|---|---|---|---|
| **Azure Speech** | Speech-to-Text | 5 hrs/month free | [portal.azure.com](https://portal.azure.com) → Create a "Speech" resource |
| **OpenAI** | LLM + tool calling | Pay-as-you-go | [platform.openai.com](https://platform.openai.com) |
| **Cartesia** | Text-to-Speech | Check current limits | [cartesia.ai](https://cartesia.ai) |

---

## 🎮 Usage

1. **Start** — Click the 🎙️ button on the avatar page
2. **Identify** — Say your 4-digit user ID (e.g., *"my ID is 1001"*)
3. **Book** — *"I'd like to book an appointment on February 12th"*
4. **Check** — *"What appointments do I have?"*
5. **Cancel** — *"Cancel my appointment"*
6. **Modify** — *"Move my 10 AM to 2 PM"*
7. **End** — *"Bye"* or *"That's all"*

### Pre-loaded Test Users

| User ID | Name | Existing Appointments |
|---|---|---|
| `1001` | Archit Ojha | Feb 10 @ 10:00 AM, Feb 14 @ 2:30 PM |
| `2045` | Priya Sharma | Feb 12 @ 11:00 AM, Feb 10 @ 3:00 PM |
| `3300` | Rahul Verma | *(none — test empty state)* |
| `9999` | *(not registered)* | *(test new user registration flow)* |

---

## 🔧 Tool Functions

| # | Tool | Trigger | What Happens |
|---|---|---|---|
| 1 | `identify_user` | User gives 4-digit ID | Looks up profile + active appointments |
| 2 | `register_user` | ID not found, user agrees | Generates new 4-digit ID, saves to DB |
| 3 | `fetch_slots` | Before any booking | Returns free 30-min slots (9 AM – 6 PM) |
| 4 | `book_appointment` | User picks a slot | Books with double-booking prevention |
| 5 | `retrieve_appointments` | "Check my appointments" | Returns active + cancelled history |
| 6 | `cancel_appointment` | User wants to cancel | Soft-delete (preserves audit trail) |
| 7 | `modify_appointment` | "Move my appointment" | Changes date/time with conflict check |
| 8 | `end_conversation` | "Bye", "done", "that's all" | Ends call → triggers summary |

Every tool call is **rendered live** on the web UI as a styled card with slot grids, appointment details, and status badges.

---

## 🖥️ Web UI Panels

| Panel | Location | What It Shows |
|---|---|---|
| **Avatar** | Left | Animated SVG face — lip-sync, blinking, breathing |
| **User Card** | Left (below avatar) | Appears after `identify_user` — name, ID, active appointments |
| **Transcript** | Right top | Live conversation — agent (purple), user (green) |
| **Tool Calls** | Right bottom | Real-time tool cards with custom rendering per type |
| **Start Overlay** | Fullscreen | Blocks until user clicks Start — required for mic |
| **Summary Overlay** | Fullscreen | Appears at call end — LLM-generated conversation summary |

---

## ⚡ Performance

| Metric | Typical |
|---|---|
| STT first partial | ~200ms |
| LLM response | 0.5–1.5s |
| TTS first byte | ~300ms |
| **End-to-end latency** | **~1.5–2.5s** |
| Summary generation | 1–3s |
| Avatar lip-sync delay | <50ms |

---

## 🧠 Design Decisions

| Decision | Why |
|---|---|
| **Multiprocessing for speaker** | PyAudio's blocking `stream.write()` would freeze the async event loop |
| **`run_in_executor` for mic** | sounddevice's blocking iterator would starve aiohttp; thread pool keeps event loop free |
| **Soft-delete cancellations** | Preserves history; agent can answer "did I cancel something?"; slot is still freed |
| **SVG avatar** | Zero cost, zero API dependency, instant load; lip-sync driven by actual TTS audio RMS |
| **Start button required** | Browser autoplay policies need a user gesture; also prevents talking to empty rooms |
| **JSON file DB** | Simple, human-readable, zero dependencies; sufficient for demo |
| **max_tokens=100** | Forces LLM to keep voice responses short (~30 words) — critical for voice UX |
| **45s silence timeout** | Long enough for user to think after agent speaks; resets while agent is talking |

---

## 📋 Known Limitations

- **Single user at a time** — one mic, one speaker, one conversation
- **JSON database** — not production-grade; no concurrent write safety
- **English-primary** — Azure STT configured for `en-US`
- **Local mic only** — no WebRTC; requires physical microphone access
- **Date edge cases** — LLM handles "tomorrow" and "next Monday" well, but ambiguous phrasing may vary
- **No auth** — any user ID can look up any appointments

---

## 📦 Dependencies

```
openai                              # LLM with function-calling
azure-cognitiveservices-speech      # Speech-to-Text
cartesia                            # Text-to-Speech
aiohttp                             # Avatar WebSocket server
sounddevice                         # Microphone capture
pyaudio                             # Audio playback
numpy                               # Audio array handling
python-dotenv                       # .env loading
```

---
