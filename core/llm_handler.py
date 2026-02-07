import os
import json
import time
from datetime import datetime
from openai import OpenAI
from utils.logger import get_custom_logger
from services.appointment_handler import APPOINTMENT_TOOLS, TOOL_DISPATCH

logger = get_custom_logger("llm_handler")

SYSTEM_PROMPT = """You are {agent_name}, a friendly AI receptionist for a medical clinic.

Today's date: {today}

═══ CALL FLOW ═══
1. GREET warmly → ask for their 4-digit user ID
2. Call identify_user with the ID
3. If found → greet by name, briefly mention appointment count (NOT details), ask what they need
4. If not found → ask if they want to register, then call register_user with their name
5. Help with booking, checking, cancelling, or modifying appointments

═══ BOOKING FLOW ═══
1. Get the date they want
2. ALWAYS call fetch_slots first to check availability
3. Pick EXACTLY 3 well-spaced slots from the results (morning, midday, afternoon) and say something like: "I have 9:30 AM, 12 PM, and 3:30 PM open. Which works for you, or would you prefer a different time?"
4. Confirm: name, date, time, purpose
5. Call book_appointment — ONLY with a slot from fetch_slots
6. Read back confirmation with appointment ID (spell it out)

⚠️ CRITICAL: NEVER list more than 3 time slots. NEVER use bullet points or numbered lists. ALWAYS respond in a single short sentence. This is a voice call — the person is LISTENING, not reading.

═══ RULES ═══
- VOICE call — MAX 30 words per response. Shorter is better.
- Ask ONE thing at a time
- NO bullet points, NO numbered lists, NO dashes — speak in natural sentences
- NEVER book without calling fetch_slots first
- NEVER invent times — only use slots from fetch_slots
- When user says bye/done/that's all → call end_conversation tool
- User IDs are 4 digits (like 1234, 5678)
- Appointment IDs are 8 characters (like a1b2c3d4)
- Be concise and natural: "Sure!", "Got it!", "Let me check..."

═══ EXTRACTION ═══
- Dates: "tomorrow", "next Monday", "12th Feb" → YYYY-MM-DD
- Times: "10 AM", "afternoon" → "10:00 AM", "02:00 PM"
- IDs: "one two three four" or "1234" → "1234"

Language: Match caller's language. Default English."""


class ConversationalLLM:
    def __init__(self, agent_name: str, language: str = "en-US"):
        self.api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        if not self.api_key:
            logger.warning("⚠️ No API key found — LLM will use fallback!")
        self.agent_name = agent_name
        self.language = language

        today = datetime.now().strftime("%A, %B %d, %Y")
        self.system_message = {
            "role": "system",
            "content": SYSTEM_PROMPT.format(agent_name=agent_name, today=today),
        }
        self.messages: list[dict] = [self.system_message]
        self.tool_call_log: list[dict] = []

    def get_response(self, user_text: str) -> dict:
        self.messages.append({"role": "user", "content": user_text})
        self.tool_call_log = []
        end_conversation = False

        if not self.client:
            return self._fallback(user_text)

        try:
            start = time.time()
            response = self._call_llm()
            msg = response.choices[0].message

            rounds = 0
            while msg.tool_calls and rounds < 5:
                rounds += 1
                self.messages.append(msg)

                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    logger.info("Tool call: %s(%s)", fn_name, fn_args)

                    if fn_name == "end_conversation":
                        end_conversation = True

                    if fn_name in TOOL_DISPATCH:
                        result = TOOL_DISPATCH[fn_name](**fn_args)
                    else:
                        result = {"error": f"Unknown function: {fn_name}"}

                    self.tool_call_log.append({
                        "function": fn_name,
                        "arguments": fn_args,
                        "result": result,
                    })

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    })

                response = self._call_llm()
                msg = response.choices[0].message

            text = (msg.content or "").strip()
            self.messages.append({"role": "assistant", "content": text})

            latency = time.time() - start
            logger.info("LLM response (%.2fs): %s", latency, text[:100])

            return {
                "response": text,
                "tool_calls": self.tool_call_log,
                "end_conversation": end_conversation,
            }

        except Exception as e:
            logger.exception("LLM error: %s", e)
            return self._fallback(user_text)

    def get_summary(self) -> str:
        if not self.client:
            return "Summary unavailable (no API key)."
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": (
                        "Summarize this phone call between an AI receptionist and a caller. "
                        "Include: caller identity (name, user ID), intent, appointments booked/cancelled/modified, "
                        "key details, outcome. 3-5 sentences."
                    )},
                    {"role": "user", "content": self._format_history()},
                ],
                temperature=0.3, max_tokens=250,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Summary failed: %s", e)
            return "Summary generation failed."

    def _call_llm(self):
        return self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            tools=APPOINTMENT_TOOLS,
            tool_choice="auto",
            temperature=0.5,
            max_tokens=100,  # enforce brevity for voice
        )

    def _fallback(self, user_text: str) -> dict:
        logger.error("❌ NO API KEY — fallback!")
        text = f"I heard '{user_text}'. Please set up an API key for full functionality."
        self.messages.append({"role": "assistant", "content": text})
        return {"response": text, "tool_calls": [], "end_conversation": False}

    def _format_history(self) -> str:
        lines = []
        for m in self.messages:
            if m["role"] == "system": continue
            if m["role"] == "user": lines.append(f"Caller: {m['content']}")
            elif m["role"] == "assistant" and m.get("content"): lines.append(f"Agent: {m['content']}")
        return "\n".join(lines)
