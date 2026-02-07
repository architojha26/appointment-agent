import os
from utils.prompts import agent_prompts
from utils.logger import get_custom_logger

try:
    from cartesia import AsyncCartesia
except Exception:
    AsyncCartesia = None

try:
    import sounddevice as sd
except Exception:
    sd = None

CARTESIA_LANGUAGE_MAP = {"hi-IN": "hi", "en-IN": "en"}

class CartesiaTTS:
    def __init__(self, agent_name, language, stats, logger, call_sid, sample_rate=16000, buffer_size=5120):
        self.api_key = os.getenv("CARTESIA_API_KEY")
        self.language = language
        self.agent_name = agent_name
        self.agent_info = agent_prompts[agent_name]
        self.voice = self.agent_info["voice"]
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.model = "sonic-2"
        self.spoken_text = []
        self.stats = stats or {}
        self.logger = logger or get_custom_logger("cartesia_tts")
        self.call_sid = call_sid
        self.all_timestamps = []

        self.client = AsyncCartesia(api_key=self.api_key) if (AsyncCartesia and self.api_key) else None
        self._ws = None

    async def generate_and_stream_speech(self, sentence, sender_cost_metrics=None, play_local=True):
        if not self.client:
            self.logger.error("%s Cartesia SDK not installed or API key missing.", self.call_sid)
            return
        if len(sentence) == 1 or sentence.strip() == '[[':
            self.spoken_text = []
            return

        self.spoken_text = []
        self.spoken_text.append(sentence)

        sentence = '<break time="0.2s" />' + sentence
        try:
            if self._ws is None:
                self._ws = await self.client.tts.websocket()

            audio_stream = await self._ws.send(
                model_id=self.model,
                transcript=sentence,
                voice={"id": self.voice},
                language=CARTESIA_LANGUAGE_MAP.get(self.language, "en"),
                output_format={"container": "raw", "encoding": "pcm_s16le", "sample_rate": self.sample_rate},
                add_timestamps=True,
                stream=True
            )
            buffer = bytearray()

            out = None
            if play_local and sd is not None:
                out = sd.RawOutputStream(samplerate=self.sample_rate, channels=1, dtype="int16")
                out.start()

            async for chunk in audio_stream:
                if chunk.audio is not None:
                    buffer.extend(chunk.audio)
                    while len(buffer) >= self.buffer_size:
                        packet = bytes(buffer[:self.buffer_size])
                        if out: out.write(packet)
                        yield packet
                        buffer = buffer[self.buffer_size:]

                if getattr(chunk, "word_timestamps", None) is not None:
                    self.all_timestamps.extend(chunk.word_timestamps.start)

            if buffer:
                packet = bytes(buffer)
                if out: out.write(packet)
                yield packet

            if out:
                out.stop(); out.close()

        except Exception as e:
            self.logger.exception("%s Cartesia error: %s", self.call_sid, e)
            if self.spoken_text: self.spoken_text.pop()

    async def close_websocket(self):
        if self._ws:
            try: await self._ws.close()
            except Exception: pass