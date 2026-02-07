import json
import time
from datetime import datetime
from pathlib import Path
from utils.logger import get_custom_logger

logger = get_custom_logger("conversation_logger")


class ConversationLogger:
    """
    Tracks every turn (user / agent) and writes to a JSON file in real time.
    """

    def __init__(self, call_id: str, log_dir: str = "logs"):
        self.call_id = call_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "full_conversation.json"
        self.start_time = datetime.now().isoformat()
        self.turns: list[dict] = []
        self._flush_meta()

    # ── Public API ────────────────────────────────────────────────────

    def log_user(self, text: str):
        turn = {
            "role": "user",
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }
        self.turns.append(turn)
        self._flush()
        logger.info("[%s] USER: %s", self.call_id, text)

    def log_agent(self, text: str, tool_calls: list[dict] | None = None):
        turn = {
            "role": "agent",
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }
        if tool_calls:
            turn["tool_calls"] = tool_calls
        self.turns.append(turn)
        self._flush()
        logger.info("[%s] AGENT: %s", self.call_id, text)

    def log_system(self, event: str, details: dict | None = None):
        turn = {
            "role": "system",
            "event": event,
            "timestamp": datetime.now().isoformat(),
        }
        if details:
            turn["details"] = details
        self.turns.append(turn)
        self._flush()

    def finalize(self, summary: str = ""):
        """Write final summary and close."""
        self.turns.append({
            "role": "system",
            "event": "conversation_ended",
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
        })
        self._flush()
        logger.info("[%s] Conversation finalized. %d turns logged.", self.call_id, len(self.turns))

    def get_turns(self) -> list[dict]:
        return self.turns

    # ── Internal ──────────────────────────────────────────────────────

    def _flush_meta(self):
        """Write initial metadata."""
        self._flush()

    def _flush(self):
        payload = {
            "call_id": self.call_id,
            "start_time": self.start_time,
            "turn_count": len(self.turns),
            "turns": self.turns,
        }
        try:
            self.log_path.write_text(json.dumps(payload, indent=2, default=str))
        except Exception as e:
            logger.error("Failed to write conversation log: %s", e)