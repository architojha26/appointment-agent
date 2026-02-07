import os
import asyncio
import audioop
import time
import multiprocessing as mp
import queue
from queue import Queue

from stt.azure_stt import SimpleAzureSTT
from core.llm_handler import ConversationalLLM
from utils.conversation_logger import ConversationLogger
from services.conversation_summarizer import ConversationSummarizer
from services.appointment_handler import save_call_summary
from utils.logger import get_custom_logger

try:
    from mic_stream import MicStream
except Exception:
    MicStream = None

logger = get_custom_logger("conversation_manager")

# ── Settings ─────────────────────────────────────────────────────────────
USER_SILENCE_TIMEOUT = 2.0     # seconds of silence before processing user speech
END_CONVERSATION_TIMEOUT = 45  # seconds of USER silence before auto-ending
GOODBYE_TTS_TIMEOUT = 15.0     # max seconds to wait for speaker to finish goodbye


async def run_conversation_manager(
    mp_commands_queue: mp.Queue,
    stop_event: mp.Event,
    term_event: mp.Event,
    agent_status_queue: mp.Queue,
    call_id: str,
    agent_name: str,
    language: str,
    avatar_queue: mp.Queue = None,
    start_event: mp.Event = None,
):
    """
    Main conversation loop:
      mic audio → Azure STT → 2s pause → LLM (with tools) → speak command → speaker process

    End-of-conversation flow:
      1. Detect end (user says bye / 30s silence)
      2. Send goodbye to speaker
      3. Wait for speaker to finish (poll agent_status_queue)
      4. Break out of mic loop
      5. Stop STT
      6. Generate summary via ConversationSummarizer
      7. Send terminate to speaker
      8. Return
    """

    # ── Mic setup ────────────────────────────────────────────────────
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        mac_mic_idx = next(
            (i for i, d in enumerate(devs) if "MacBook Pro Microphone" in d["name"]),
            None,
        )
        if mac_mic_idx is not None:
            sd.default.device = (mac_mic_idx, None)
            print(f"[conv_mgr] 🎙️ Using: {devs[mac_mic_idx]['name']}")
    except Exception as e:
        print(f"[conv_mgr] ⚠️ Mic setup warning: {e}")

    # ── STT ──────────────────────────────────────────────────────────
    stt_queue = Queue()
    stt = SimpleAzureSTT(
        threshold=50,
        uuid=call_id,
        stop_event=stop_event,
        stt_queue=stt_queue,
        language=language,
        sample_rate=16000,
    )

    if not await stt.start():
        print("[conv_mgr] ❌ Failed to start Azure STT")
        return

    if MicStream is None:
        print("[conv_mgr] ❌ MicStream not available")
        return

    mic = MicStream(samplerate=16000, blocksize=320, channels=1, dtype="int16")

    # ── LLM + Logger + Summarizer ────────────────────────────────────
    llm = ConversationalLLM(agent_name=agent_name, language=language)
    conv_logger = ConversationLogger(call_id=call_id)
    summarizer = ConversationSummarizer()

    conv_logger.log_system("conversation_started", {"agent": agent_name, "language": language})

    # ── State ────────────────────────────────────────────────────────
    turn_id = 0
    chunk_count = 0
    agent_is_speaking = False
    pending_user_text: list[str] = []
    last_stt_time: float | None = None
    last_activity_time = time.time()
    identified_user_id: str | None = None  # track who's on the call

    def avatar_event(evt: dict):
        if avatar_queue is None:
            return
        try:
            avatar_queue.put_nowait(evt)
        except Exception:
            pass

    # ── Wait for browser to click Start ──────────────────────────────
    if start_event:
        print("[conv_mgr] ⏳ Waiting for 'Start Conversation' in browser...")
        while not start_event.is_set() and not term_event.is_set():
            await asyncio.sleep(0.2)
        if term_event.is_set():
            return
        print("[conv_mgr] ▶️ Conversation started!")

    conv_logger.log_agent(
        "Hello! Welcome to our clinic. "
        "Could you please share your 4-digit user ID?"
    )

    # Wait for speaker to finish greeting
    _wait_for_ready(agent_status_queue, timeout=15)
    last_activity_time = time.time()  # reset after greeting
    avatar_event({"type": "listening"})
    print("[conv_mgr] ✅ Ready — speak anytime!")

    # ── Main conversation loop ───────────────────────────────────────
    # mic.stream() is a blocking generator — we read chunks via executor
    # so the event loop stays free for the avatar server (aiohttp).
    loop = asyncio.get_event_loop()
    mic_iter = mic.stream()

    def _next_chunk():
        """Read one mic chunk (blocking, runs in thread pool)."""
        try:
            return next(mic_iter)
        except StopIteration:
            return None

    while not term_event.is_set():
        # Non-blocking mic read
        chunk = await loop.run_in_executor(None, _next_chunk)
        if chunk is None:
            break

        chunk_count += 1
        now = time.time()

        # Periodic audio debug
        if chunk_count % 100 == 0:
            rms = audioop.rms(chunk, 2)
            if rms > 30:
                print(f"[conv_mgr] 🔊 Audio RMS: {rms}")

        # Feed audio to STT
        await stt.send_audio(chunk)

        # ── Drain agent status ───────────────────────────────────────
        try:
            while True:
                status = agent_status_queue.get_nowait()
                action = status.get("action")
                if action == "speaking":
                    agent_is_speaking = True
                    last_activity_time = now
                elif action in ("done_speaking", "interrupted", "ready"):
                    agent_is_speaking = False
                    last_activity_time = now
                    if action in ("done_speaking", "interrupted"):
                        avatar_event({"type": "listening"})
        except queue.Empty:
            pass

        # Keep timer alive while agent is still speaking
        if agent_is_speaking:
            last_activity_time = now

        # ── Drain STT results ────────────────────────────────────────
        try:
            while True:
                text = stt_queue.get_nowait()
                if text:
                    print(f"[conv_mgr] 🗣️ Heard: '{text}'")
                    pending_user_text.append(text)
                    last_stt_time = now
                    last_activity_time = now

                    if agent_is_speaking:
                        stop_event.set()
                        agent_is_speaking = False
                        print("[conv_mgr] 🛑 Interrupted agent")
        except queue.Empty:
            pass

        # ── 2-second pause → process user utterance ──────────────────
        if pending_user_text and last_stt_time and (now - last_stt_time >= USER_SILENCE_TIMEOUT):
            full_user_text = " ".join(pending_user_text)
            pending_user_text.clear()
            last_stt_time = None

            turn_id += 1
            print(f"\n[conv_mgr] ━━━ Turn {turn_id} ━━━")
            print(f"[conv_mgr] 👤 User: {full_user_text}")
            conv_logger.log_user(full_user_text)
            avatar_event({"type": "user_speaking", "text": full_user_text})

            # LLM response (with function-calling)
            llm_result = llm.get_response(full_user_text)
            agent_text = llm_result["response"]
            tool_calls = llm_result.get("tool_calls", [])
            end_conv = llm_result.get("end_conversation", False)

            print(f"[conv_mgr] 🤖 Agent: {agent_text}")
            conv_logger.log_agent(agent_text, tool_calls=tool_calls if tool_calls else None)

            if tool_calls:
                for tc in tool_calls:
                    print(f"[conv_mgr] 🔧 Tool: {tc['function']}({tc['arguments']}) → {tc['result']}")

                    # Track identified user
                    if tc["function"] == "identify_user" and tc["result"].get("status") == "found":
                        identified_user_id = tc["result"]["user"]["user_id"]
                        print(f"[conv_mgr] 👤 Identified user: {identified_user_id}")
                    elif tc["function"] == "register_user" and tc["result"].get("status") == "registered":
                        identified_user_id = tc["result"]["user_id"]
                        print(f"[conv_mgr] 👤 Registered user: {identified_user_id}")

                    # Send tool call to avatar UI
                    avatar_event({
                        "type": "tool_call",
                        "function": tc["function"],
                        "arguments": tc["arguments"],
                        "result": tc["result"],
                    })

            # Send to speaker
            stop_event.clear()
            last_activity_time = time.time()  # reset — agent is responding
            try:
                mp_commands_queue.put_nowait({
                    "action": "speak",
                    "text": agent_text,
                    "turn_id": turn_id,
                })
            except Exception:
                print("[conv_mgr] ❌ Failed to send speak command")

            # ── End of conversation? ─────────────────────────────────
            if end_conv:
                print("[conv_mgr] 👋 End of conversation detected.")
                _wait_for_speaker_done(agent_status_queue, timeout=GOODBYE_TTS_TIMEOUT)
                break

        # ── Auto-end on prolonged silence ────────────────────────────
        if (now - last_activity_time) > END_CONVERSATION_TIMEOUT and not agent_is_speaking:
            print(f"[conv_mgr] ⏰ No user activity for {END_CONVERSATION_TIMEOUT}s, ending...")
            turn_id += 1
            goodbye = "I haven't heard from you in a while. I'll end the call now. Feel free to call back anytime. Goodbye!"
            conv_logger.log_agent(goodbye)
            try:
                mp_commands_queue.put_nowait({"action": "speak", "text": goodbye, "turn_id": turn_id})
            except Exception:
                pass
            _wait_for_speaker_done(agent_status_queue, timeout=GOODBYE_TTS_TIMEOUT)
            break

    # ══════════════════════════════════════════════════════════════════
    #  POST-LOOP: Stop STT → Summarize → Terminate speaker → Exit
    # ══════════════════════════════════════════════════════════════════

    # Step 1: Stop STT
    print("\n[conv_mgr] 🔇 Stopping STT...")
    await stt.stop()

    # Step 2: Generate summary
    print("[conv_mgr] 📝 Generating conversation summary...")
    summary = summarizer.summarize_from_turns(conv_logger.get_turns())
    conv_logger.finalize(summary=summary)

    # Save summary to call_summaries.json
    if identified_user_id:
        save_call_summary(user_id=identified_user_id, call_id=call_id, summary=summary)
        print(f"[conv_mgr] 💾 Summary saved for user {identified_user_id}")

    # Send summary to avatar (include user_id so frontend can label it)
    avatar_event({
        "type": "summary",
        "text": summary,
        "user_id": identified_user_id,
        "call_id": call_id,
    })

    print("\n" + "=" * 60)
    print("📋 CONVERSATION SUMMARY")
    print("=" * 60)
    print(summary)
    print("=" * 60 + "\n")

    # Step 3: Terminate speaker process
    print("[conv_mgr] 🛑 Sending terminate to speaker...")
    try:
        mp_commands_queue.put_nowait({"action": "terminate"})
    except Exception:
        pass

    # Step 4: Signal main.py we're done
    term_event.set()
    print("[conv_mgr] ✅ Conversation manager finished.")


# ── Helpers ──────────────────────────────────────────────────────────────

def _wait_for_ready(agent_status_queue: mp.Queue, timeout: float = 15):
    """Block until the speaker signals 'ready' (greeting done)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = agent_status_queue.get(timeout=0.5)
            if status.get("action") == "ready":
                return
        except queue.Empty:
            continue
    print("[conv_mgr] ⚠️ Timed out waiting for speaker ready signal")


def _wait_for_speaker_done(agent_status_queue: mp.Queue, timeout: float = 15):
    """
    Block until the speaker signals 'done_speaking'.
    This ensures the goodbye TTS finishes before we tear everything down.
    """
    print("[conv_mgr] ⏳ Waiting for agent to finish speaking...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = agent_status_queue.get(timeout=0.5)
            action = status.get("action")
            if action == "done_speaking":
                print("[conv_mgr] ✅ Agent finished speaking.")
                return
            elif action == "interrupted":
                print("[conv_mgr] ✅ Agent was interrupted, proceeding.")
                return
        except queue.Empty:
            continue
    print("[conv_mgr] ⚠️ Timed out waiting for speaker to finish, proceeding anyway.")
