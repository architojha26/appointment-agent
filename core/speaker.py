import os
import sys
import uuid as uuidlib
import asyncio
import audioop
import multiprocessing as mp
import queue
from dotenv import load_dotenv

from utils.logger import get_custom_logger
from tts.cartesia_tts import CartesiaTTS

logger = get_custom_logger("speaker")

# Max RMS we expect from TTS audio (for normalization)
MAX_TTS_RMS = 8000


def speaker_proc(
    mp_commands_queue: mp.Queue,
    stop_event: mp.Event,
    term_event: mp.Event,
    agent_status_queue: mp.Queue,
    agent: str,
    language: str,
    avatar_queue: mp.Queue = None,
    start_event: mp.Event = None,
):
    """
    Speaker process: speaks agent responses via TTS.
    Waits for commands from the main process (conversation manager).

    Commands:
      {"action": "speak", "text": "...", "turn_id": ...}
      {"action": "terminate"}
    """

    async def run_speaker():
        load_dotenv()
        call_id = str(uuidlib.uuid4())[:8]
        stats = {}
        tts_cost = {"tts_cost": 0.0}

        def avatar_event(evt: dict):
            """Send event to avatar server (non-blocking)."""
            if avatar_queue is None:
                return
            try:
                avatar_queue.put_nowait(evt)
            except Exception:
                pass

        tts = CartesiaTTS(
            agent_name=agent,
            language=language,
            stats=stats,
            logger=logger,
            call_sid=call_id,
        )

        # ── Wait for start signal from browser ────────────────────────
        if start_event:
            print("[speaker] ⏳ Waiting for 'Start Conversation' click in browser...")
            while not start_event.is_set() and not term_event.is_set():
                await asyncio.sleep(0.2)
            if term_event.is_set():
                return
            print("[speaker] ▶️ Start signal received!")

        # ── Greeting ─────────────────────────────────────────────────
        greeting = (
            "Hello! Welcome to our clinic. "
            "Could you please share your 4-digit user ID?"
        )
        print(f"[speaker] 🗣️ {greeting}")

        try:
            agent_status_queue.put_nowait({"action": "speaking", "text": greeting})
        except Exception:
            pass
        avatar_event({"type": "speaking_start", "text": greeting})

        async for chunk in tts.generate_and_stream_speech(
            greeting, sender_cost_metrics=tts_cost, play_local=True
        ):
            if stop_event.is_set() or term_event.is_set():
                break
            # Send audio energy to avatar
            if chunk and len(chunk) >= 2:
                rms = audioop.rms(chunk, 2)
                energy = min(1.0, rms / MAX_TTS_RMS)
                avatar_event({"type": "audio_energy", "energy": energy})

        avatar_event({"type": "speaking_end"})

        # Signal greeting done → main process can start listening
        try:
            agent_status_queue.put_nowait({"action": "ready"})
        except Exception:
            pass

        # ── Main command loop ────────────────────────────────────────
        while not term_event.is_set():
            # Wait for a command from conversation manager
            try:
                command = mp_commands_queue.get(timeout=0.3)
            except queue.Empty:
                continue

            action = command.get("action")

            if action == "terminate":
                print("[speaker] 🛑 Terminate command received")
                break

            if action == "speak":
                text = command["text"]
                turn_id = command.get("turn_id", "?")
                print(f"[speaker] 🗣️ Turn {turn_id}: {text}")

                # Clear stop event before speaking
                stop_event.clear()

                try:
                    agent_status_queue.put_nowait({"action": "speaking", "text": text})
                except Exception:
                    pass
                avatar_event({"type": "speaking_start", "text": text})

                interrupted = False
                async for chunk in tts.generate_and_stream_speech(
                    text, sender_cost_metrics=tts_cost, play_local=True
                ):
                    if stop_event.is_set() or term_event.is_set():
                        interrupted = True
                        print(f"[speaker] 🛑 Interrupted during turn {turn_id}")
                        break
                    # Send audio energy to avatar
                    if chunk and len(chunk) >= 2:
                        rms = audioop.rms(chunk, 2)
                        energy = min(1.0, rms / MAX_TTS_RMS)
                        avatar_event({"type": "audio_energy", "energy": energy})

                avatar_event({"type": "speaking_end"})

                if interrupted:
                    try:
                        agent_status_queue.put_nowait({"action": "interrupted", "turn_id": turn_id})
                    except Exception:
                        pass
                else:
                    try:
                        agent_status_queue.put_nowait({"action": "done_speaking", "turn_id": turn_id})
                    except Exception:
                        pass

        # ── Cleanup ──────────────────────────────────────────────────
        avatar_event({"type": "shutdown"})
        await tts.close_websocket()
        print("[speaker] ✅ Speaker process exiting.")

    try:
        asyncio.run(run_speaker())
    except Exception as e:
        print(f"[speaker] Fatal error: {e}", file=sys.stderr)
