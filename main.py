"""
Voice AI Appointment Booking Agent
===================================
Entry point. Spawns speaker process + conversation manager + avatar server.
Conversation starts ONLY when user clicks Start in the browser.

Usage:
    python main.py
    Open http://localhost:8765 and click "Start Conversation"
"""

import os
import sys
import uuid as uuidlib
import asyncio
import multiprocessing as mp
import signal
from dotenv import load_dotenv

from core.speaker import speaker_proc
from core.conversation_manager import run_conversation_manager
from avatar.server import AvatarServer


async def async_main(
    mp_commands_queue, stop_event, term_event, agent_status_queue,
    avatar_queue, start_event, call_id, agent, language,
):
    """Run avatar server + conversation manager concurrently."""
    avatar_server = AvatarServer(
        avatar_queue=avatar_queue,
        start_event=start_event,
    )
    await avatar_server.start()

    try:
        await run_conversation_manager(
            mp_commands_queue=mp_commands_queue,
            stop_event=stop_event,
            term_event=term_event,
            agent_status_queue=agent_status_queue,
            call_id=call_id,
            agent_name=agent,
            language=language,
            avatar_queue=avatar_queue,
            start_event=start_event,
        )
    finally:
        await avatar_server.stop()


def main():
    load_dotenv()

    agent = os.getenv("AGENT_NAME", "kavita")
    language = "en-US"
    call_id = str(uuidlib.uuid4())[:8]
    avatar_port = int(os.getenv("AVATAR_PORT", "8765"))

    # ── Shared IPC objects ───────────────────────────────────────────
    term_event = mp.Event()    # full shutdown
    stop_event = mp.Event()    # interrupt current TTS
    start_event = mp.Event()   # browser triggers conversation start

    manager = mp.Manager()
    mp_commands_queue = manager.Queue()
    agent_status_queue = manager.Queue()
    avatar_queue = manager.Queue()

    # ── Signal handling ──────────────────────────────────────────────
    def signal_handler(sig, frame):
        print("\n[main] 🛑 Shutting down...")
        term_event.set()
        stop_event.set()
        start_event.set()  # unblock if waiting

    signal.signal(signal.SIGINT, signal_handler)

    # ── Start speaker process ────────────────────────────────────────
    speaker = mp.Process(
        target=speaker_proc,
        args=(mp_commands_queue, stop_event, term_event, agent_status_queue,
              agent, language, avatar_queue, start_event),
        daemon=True,
    )
    speaker.start()

    print(f"[main] 🚀 Appointment Booking Agent started (call_id={call_id})")
    print(f"[main] 🔊 Speaker PID: {speaker.pid}")
    print(f"[main] 🎭 Avatar: http://localhost:{avatar_port}")
    print("[main] 🌐 Open the URL and click 'Start Conversation' to begin")
    print("[main] 🛑 Press Ctrl+C to stop\n")

    try:
        asyncio.run(
            async_main(
                mp_commands_queue=mp_commands_queue,
                stop_event=stop_event,
                term_event=term_event,
                agent_status_queue=agent_status_queue,
                avatar_queue=avatar_queue,
                start_event=start_event,
                call_id=call_id,
                agent=agent,
                language=language,
            )
        )
    except KeyboardInterrupt:
        pass
    finally:
        term_event.set()
        stop_event.set()
        start_event.set()
        try:
            mp_commands_queue.put_nowait({"action": "terminate"})
        except Exception:
            pass
        if speaker.is_alive():
            speaker.terminate()
        speaker.join(timeout=3)
        print("[main] ✅ Appointment Booking Agent stopped.")


if __name__ == "__main__":
    main()
