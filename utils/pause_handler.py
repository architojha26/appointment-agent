import os
import json
import time
import random
import asyncio
from openai import AsyncOpenAI

FAST_INTERRUPT_STOP_KEYWORDS = {
    "stop", "ruko", "ruk", "ruk jao", "ek minute", "hold", "wait", "bas", "enough", "chup", "mute", "pause", "band"
}

class PauseDetection:
    def __init__(self, stop_event, logger, uuid, language):
        # Keep using your env var name LLM_API_KEY for compatibility
        self.llm_api_key = os.getenv("LLM_API_KEY")
        self.language = language
        self.previous_word = {}
        # AsyncOpenAI client (no custom base_url here; it uses OpenAI)
        self.client = AsyncOpenAI(api_key=self.llm_api_key) if self.llm_api_key else None
        self.logger = logger
        self.call_sid = uuid

        # System prompt instructing the model to return a JSON with "to_stop"
        self.system_prompt = """Given below is a sentence spoken by two persons. 
        Person 1: {msg1}
        Person 2: {msg2}
        Does person2 wants person1 to stop speaking? Person 2 may want person 1 to stop speaking if they are disagreeing. If person 2 is just agreeing or saying hello then he want person 1 to continue. 
        Give output in json in syntax:
        {{
            "to_stop": true_or_false
        }}

        Your output should only and only contain a json in the above format and nothing else.
        """

        # thresholds (unchanged)
        raw = '{"default": 7, "hi-IN": 8, "hi": 8}'
        try:
            thresholds = json.loads(raw)
        except json.JSONDecodeError:
            thresholds = {"default": int(raw)}
        self.max_characters_for_pausing = thresholds.get(language, thresholds.get("default", 7))

        # choose model from env or fallback
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.stop_event = stop_event

    async def pause(self, msg1, msg2, user_sentences_detected=100):
        try:
            self.logger.info("%s: Agent `%s`, User `%s` | len(user)=%s | lang=%s",
                             self.call_sid, msg1, msg2, len(msg2), self.language)

            # Simple heuristics first (unchanged)
            if len(msg2) > self.max_characters_for_pausing:
                self.stop_event.set()
                return True
            if user_sentences_detected < 1:
                self.stop_event.set()
                return True
            if msg2 in self.previous_word:
                cached_msg1, cached_stop = self.previous_word[msg2]
                if cached_msg1 == msg1:
                    if cached_stop:
                        self.stop_event.set()
                    return cached_stop
            if self.fast_interrupt_check(msg2):
                self.stop_event.set()
                return True

            # Call LLM (with timeout)
            start = time.time()
            self.logger.info("%s: Calling OpenAI for pause detection", self.call_sid)
            to_stop = await asyncio.wait_for(self._make_api_call(msg1, msg2), timeout=0.4)
            self.logger.info("%s: Pause latency: %.3fs", self.call_sid, time.time() - start)

            # Cache and set event if needed
            self.previous_word = {msg2: (msg1, bool(to_stop))}
            if to_stop:
                self.stop_event.set()
            return bool(to_stop)

        except asyncio.TimeoutError:
            # Keep behavior: random fallback on timeout
            return random.choice([True, False])
        except Exception as e:
            self.logger.exception("%s: Pause detection error: %s", self.call_sid, e)
            return random.choice([True, False])

    async def _make_api_call(self, msg1, msg2):
        """
        Calls OpenAI via AsyncOpenAI.chat.completions.create and expects a JSON response
        like: {"to_stop": true}
        Returns a python boolean (True/False) or falls back to heuristics if parsing fails.
        """
        if not self.client:
            # No API key configured — fallback to random to preserve behavior
            self.logger.warning("%s: No LLM client configured (LLM_API_KEY missing). Falling back.", self.call_sid)
            return random.choice([True, False])

        system_content = self.system_prompt.format(msg1=msg1, msg2=msg2)
        messages = [{"role": "system", "content": system_content}]

        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=32
            )
            # The SDK returns choices; extract text
            text = ""
            try:
                text = resp.choices[0].message.content.strip()
            except Exception:
                # Some SDKs may return slightly different structure; try to stringify whole resp
                text = str(resp)

            # Try to parse a JSON blob from the model output
            # First try direct JSON parse
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "to_stop" in parsed:
                    return bool(parsed["to_stop"])
            except json.JSONDecodeError:
                # If the model returned extra whitespace or text, try to find the first {...} substring
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        parsed = json.loads(text[start:end+1])
                        if isinstance(parsed, dict) and "to_stop" in parsed:
                            return bool(parsed["to_stop"])
                    except json.JSONDecodeError:
                        pass

            # Fallback: try to infer from plain text (yes/no/true/false)
            lowered = text.lower()
            if "true" in lowered or "yes" in lowered or "stop" in lowered or "please stop" in lowered:
                return True
            if "false" in lowered or "no" in lowered or "continue" in lowered:
                return False

            # Last-resort fallback: random choice to keep system moving
            return random.choice([True, False])

        except Exception as e:
            # Bubble up to caller which has a broad exception catch and returns random fallback
            self.logger.exception("%s: OpenAI call failed: %s", self.call_sid, e)
            raise

    def fast_interrupt_check(self, msg):
        return bool(set(msg.lower().split()).intersection(FAST_INTERRUPT_STOP_KEYWORDS))
