"""
Conversation Summarizer
========================
Generates a structured summary at the end of a conversation.
Covers: caller intent, appointments acted on, key details, outcome.
"""

import os
import json
import time
from datetime import datetime
from openai import OpenAI
from utils.logger import get_custom_logger

logger = get_custom_logger("summarizer")

SUMMARY_SYSTEM_PROMPT = """You are a conversation summarizer for a clinic/office reception AI.

Given the transcript of a phone call between an AI receptionist and a caller, produce a structured summary.

Output format (plain text, not JSON):

CALLER INTENT: What the caller wanted (1 line)
ACTIONS TAKEN: List any appointments booked, cancelled, checked, or slots queried. Include appointment IDs, dates, times, names if available. Say "None" if no actions were taken.
KEY DETAILS: Important info mentioned (names, dates, special requests, concerns)
OUTCOME: How the call ended (resolved, pending, caller hung up, etc.)
DURATION: {turn_count} turns

Keep each section to 1-2 sentences max. Be factual, not flowery."""


class ConversationSummarizer:
    """
    Standalone summarizer that can work with:
      - ConversationLogger turns (list of dicts with role/text)
      - Raw LLM message history (list of openai-style messages)
    """

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None

    def summarize_from_turns(self, turns: list[dict]) -> str:
        """
        Summarize from ConversationLogger turns.
        Each turn: {"role": "user"|"agent"|"system", "text": "...", "timestamp": "...", ...}
        """
        transcript = self._turns_to_transcript(turns)
        turn_count = sum(1 for t in turns if t.get("role") in ("user", "agent"))
        return self._generate_summary(transcript, turn_count)

    def summarize_from_messages(self, messages: list[dict]) -> str:
        """
        Summarize from OpenAI-style message history.
        Each message: {"role": "user"|"assistant"|"system"|"tool", "content": "..."}
        """
        transcript = self._messages_to_transcript(messages)
        turn_count = sum(1 for m in messages if m.get("role") in ("user", "assistant") and m.get("content"))
        return self._generate_summary(transcript, turn_count)

    # ── Core summary generation ──────────────────────────────────────

    def _generate_summary(self, transcript: str, turn_count: int) -> str:
        if not self.client:
            logger.error("No API key — cannot generate summary")
            return self._offline_summary(transcript, turn_count)

        if not transcript.strip():
            return "No conversation to summarize."

        try:
            start = time.time()
            system = SUMMARY_SYSTEM_PROMPT.replace("{turn_count}", str(turn_count))

            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Transcript:\n\n{transcript}"},
                ],
                temperature=0.2,
                max_tokens=300,
            )
            summary = resp.choices[0].message.content.strip()
            latency = time.time() - start
            logger.info("Summary generated in %.2fs (%d chars)", latency, len(summary))
            return summary

        except Exception as e:
            logger.exception("Summary generation failed: %s", e)
            return self._offline_summary(transcript, turn_count)

    # ── Transcript formatters ────────────────────────────────────────

    def _turns_to_transcript(self, turns: list[dict]) -> str:
        lines = []
        for t in turns:
            role = t.get("role", "")
            if role == "user":
                lines.append(f"Caller: {t.get('text', '')}")
            elif role == "agent":
                text = t.get("text", "")
                lines.append(f"Agent: {text}")
                # Include tool call info if present
                if t.get("tool_calls"):
                    for tc in t["tool_calls"]:
                        fn = tc.get("function", "?")
                        args = tc.get("arguments", {})
                        result = tc.get("result", {})
                        lines.append(f"  [Tool: {fn}({json.dumps(args)}) → {json.dumps(result)}]")
            # Skip system events in transcript
        return "\n".join(lines)

    def _messages_to_transcript(self, messages: list[dict]) -> str:
        lines = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "user" and content:
                lines.append(f"Caller: {content}")
            elif role == "assistant" and content:
                lines.append(f"Agent: {content}")
            elif role == "tool" and content:
                try:
                    data = json.loads(content)
                    lines.append(f"  [Tool result: {json.dumps(data, default=str)}]")
                except Exception:
                    lines.append(f"  [Tool result: {content}]")
        return "\n".join(lines)

    # ── Offline fallback (no API key) ────────────────────────────────

    def _offline_summary(self, transcript: str, turn_count: int) -> str:
        """Best-effort summary without LLM."""
        lines = transcript.strip().split("\n")
        caller_lines = [l for l in lines if l.startswith("Caller:")]
        agent_lines = [l for l in lines if l.startswith("Agent:")]
        tool_lines = [l for l in lines if l.strip().startswith("[Tool")]

        parts = [
            f"CALLER INTENT: {caller_lines[0] if caller_lines else 'Unknown'}",
            f"ACTIONS TAKEN: {len(tool_lines)} tool call(s) made" if tool_lines else "ACTIONS TAKEN: None",
            f"KEY DETAILS: {turn_count} conversation turns",
            f"OUTCOME: Conversation ended",
            f"DURATION: {turn_count} turns",
            "",
            "(Note: Detailed summary unavailable — no OPENAI_API_KEY or LLM_API_KEY configured)",
        ]
        return "\n".join(parts)
