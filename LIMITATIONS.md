# Known Limitations & Future Improvements

## 🔴 Current Limitations

### 1. Avatar Lip-Sync Not Fully Synchronized
The SVG avatar's mouth movements are driven by TTS audio energy (RMS values), which provides approximate lip-sync. However:
- Mouth shapes don't map to actual phonemes (no viseme data)
- There is a slight delay between audio playback and mouth animation due to the queue-based architecture (audio plays on speaker process → energy sent to avatar queue → WebSocket → browser render)
- The avatar doesn't show facial expressions matching conversation context (e.g., smiling when confirming a booking)

**What could improve it:**
- Use Cartesia's viseme output (if available) for phoneme-accurate mouth shapes
- Integrate a video avatar service like Tavus or Beyond Presence for realistic face rendering
- Reduce the pipeline hops by measuring energy in the browser itself via Web Audio API

### 2. Single Concurrent User
The system supports only one conversation at a time because:
- One physical microphone is captured
- One PyAudio speaker output stream is used
- The speaker runs as a single subprocess

**What could improve it:**
- Move to WebRTC-based audio (browser mic → server) instead of local sounddevice
- Use a framework like LiveKit Agents that handles room-based multi-user sessions
- Containerize per-session speaker processes

### 3. JSON File Database
Appointments and summaries are stored in flat JSON files (`data/appointments.json`, `data/call_summaries.json`):
- No concurrent write safety — simultaneous writes could corrupt data
- No indexing — performance degrades with thousands of records
- No backup or replication

**What could improve it:**
- Migrate to Supabase (PostgreSQL) or SQLite for structured storage
- Add file locking or use a proper ORM
- Implement periodic backups

### 4. No Authentication / Authorization
- Any user ID can look up any other user's appointments
- No PIN, password, or voice biometric verification
- No role-based access control

**What could improve it:**
- Add a PIN or OTP verification step after user ID lookup
- Implement voice biometrics for speaker verification
- Add admin vs. user role separation

### 5. English Only
Azure STT is configured for `en-US`. Indian English accents work reasonably well, but:
- Hindi, Tamil, or other Indian language utterances are not recognized
- Code-switching (Hindi-English mix) is not handled
- No language detection or auto-switching

**What could improve it:**
- Enable multilingual recognition in Azure STT
- Add Deepgram as an alternative STT with better multilingual support
- Implement language detection and dynamic STT config switching

### 6. Local Microphone Only
Audio input comes from the local machine's microphone via `sounddevice`:
- Cannot work as a web-deployed application (no remote audio)
- Requires the user to be physically at the machine running the server

**What could improve it:**
- Implement WebRTC audio capture from the browser
- Use LiveKit's WebRTC infrastructure for remote audio streaming
- Deploy as a SIP-based telephony agent for actual phone calls

### 7. Date Parsing Handled by LLM
Natural language dates ("next Monday", "day after tomorrow") are interpreted by the LLM:
- Edge cases like "this Friday" vs "next Friday" may be ambiguous
- LLM may occasionally pick wrong dates near month/year boundaries
- No timezone awareness — uses server time

**What could improve it:**
- Add a dedicated date parsing library (e.g., `dateparser`, `dateutil`)
- Validate LLM-parsed dates with a confirmation step
- Make timezone configurable via .env

### 8. No Persistent Sessions
- Closing the browser tab doesn't pause the conversation — the backend keeps running
- Refreshing the page loses all UI state (transcript, tool cards, user card)
- The conversation cannot be resumed after disconnect

**What could improve it:**
- Store conversation state server-side, keyed by session ID
- Implement WebSocket reconnection with state replay
- Add a "resume conversation" option

### 9. No Call Recording / Export
- Conversation audio is not recorded
- The transcript is only shown in the browser UI and logged to a file
- No way to export or share conversation records

**What could improve it:**
- Record audio chunks to WAV files during the call
- Add a "Download Transcript" button in the UI
- Store transcripts alongside summaries in the database

### 10. TTS Response Length Control
Despite `max_tokens=100` and prompt instructions, the LLM occasionally generates responses that are too long for natural voice delivery:
- Listing all available slots instead of picking 3
- Including unnecessary details in confirmations

**What could improve it:**
- Post-processing truncation before TTS
- Streaming LLM responses with a word-count cutoff
- Fine-tuning or few-shot examples for concise voice-style output

---

## 🟡 Minor Issues

| Issue | Description |
|---|---|
| **Avatar breathing** | Breathing animation is a simple CSS loop, not synced to conversation state |
| **Eye blink timing** | Random intervals — doesn't respond to conversation context |
| **Tool card overflow** | Many tool calls can push the panel beyond viewport |
| **Summary overlay scroll** | Very long summaries may overflow on small screens |
| **Cancellation audit** | Soft-deleted appointments appear in retrieve but not clearly marked as cancelled in voice response |

---

## 🟢 What Works Well

| Feature | Status |
|---|---|
| End-to-end voice conversation | ✅ Stable, <2.5s latency |
| All 8 appointment tools | ✅ Fully functional |
| Conflict detection | ✅ Prevents double-booking |
| Interruption handling | ✅ User can cut in mid-speech |
| Tool call visualization | ✅ Real-time cards in browser |
| Last call summary recall | ✅ Persists across sessions |
| Current call summary | ✅ Generated and displayed at end |
| Start button flow | ✅ Clean browser-initiated start |
| Auto-timeout (45s) | ✅ Graceful goodbye on silence |
