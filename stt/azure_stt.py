import os
import asyncio
import time
import audioop
from queue import Queue
from utils.logger import get_custom_logger

try:
    import azure.cognitiveservices.speech as speechsdk
except Exception:
    speechsdk = None

azure_stt_logger = get_custom_logger("azure_stt")

class SimpleAzureSTT:
    """
    Simplified Azure STT - mirrors the working debug script exactly
    """

    def __init__(
        self,
        threshold,
        uuid,
        stop_event,
        stt_queue: Queue,
        language,
        sample_rate: int = 16000,
    ):
        if not speechsdk:
            raise RuntimeError("azure.cognitiveservices.speech not installed")

        self.subscription_key = os.getenv("AZURE_SPEECH_KEY")
        self.region = os.getenv("AZURE_SPEECH_REGION")
        if not self.subscription_key or not self.region:
            raise RuntimeError("AZURE_SPEECH_KEY or AZURE_SPEECH_REGION missing")

        self.language = "en-US"
        self.threshold = int(threshold)
        self.sample_rate = sample_rate
        self.call_sid = uuid
        self.stop_event = stop_event
        self.stt_queue = stt_queue
        self.last_spoken = None
        
        # Debug counters
        self._audio_debug_count = 0
        self._voice_activity_logged = False

        print(f"[azure_stt] Init: region={self.region}, language={self.language}, sr={self.sample_rate}Hz, threshold={self.threshold}")

        # Speech config
        self.speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key, region=self.region
        )
        self.speech_config.speech_recognition_language = self.language

        # Push-stream setup
        self.audio_stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=self.sample_rate,
            bits_per_sample=16,
            channels=1,
        )
        self.push_stream = speechsdk.audio.PushAudioInputStream(
            stream_format=self.audio_stream_format
        )
        self.audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)
        self.speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=self.speech_config, audio_config=self.audio_config
        )

        self._setup_handlers()

    def _setup_handlers(self):
        """Setup event handlers"""
        
        def on_recognizing(evt):
            if evt.result.text:
                print(f"[stt:recognizing] {evt.result.text}")

        def on_recognized(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                text = evt.result.text.strip()
                if text:
                    print(f"[stt:final] User said: {text}")
                    try:
                        self.stt_queue.put(text)
                        print("[azure_stt] STT result queued")
                    except Exception as e:
                        print(f"[azure_stt] Error putting in queue: {e}")
            elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                print("[stt:final] No speech recognized")
            else:
                print(f"[stt:final] Other reason: {evt.result.reason}")

        def on_speech_start(evt):
            print("[azure_stt] 🎤 SPEECH START DETECTED")

        def on_speech_end(evt):
            print("[azure_stt] 🎤 SPEECH END DETECTED")

        def on_session_started(evt):
            print(f"[azure_stt] Session started: {evt.session_id}")

        def on_session_stopped(evt):
            print(f"[azure_stt] Session stopped: {evt.session_id}")

        def on_canceled(evt):
            print(f"[azure_stt] Canceled: {evt.reason}")
            if hasattr(evt, 'error_details') and evt.error_details:
                print(f"[azure_stt] Error details: {evt.error_details}")

        self.speech_recognizer.recognizing.connect(on_recognizing)
        self.speech_recognizer.recognized.connect(on_recognized)
        self.speech_recognizer.speech_start_detected.connect(on_speech_start)
        self.speech_recognizer.speech_end_detected.connect(on_speech_end)
        self.speech_recognizer.session_started.connect(on_session_started)
        self.speech_recognizer.session_stopped.connect(on_session_stopped)
        self.speech_recognizer.canceled.connect(on_canceled)

    async def start(self):
        try:
            print("[azure_stt] Starting continuous recognition...")
            self.speech_recognizer.start_continuous_recognition_async().get()
            print("[azure_stt] Recognition started successfully")
            return True
        except Exception as e:
            print(f"[azure_stt] Failed to start: {e}")
            return False

    async def send_audio(self, data: bytes):
        try:
            energy = audioop.rms(data, 2)
            if energy > self.threshold:
                self.last_spoken = time.time()
                if not self._voice_activity_logged:
                    self._voice_activity_logged = True
                    print(f"[azure_stt] Voice activity detected! RMS={energy} (threshold={self.threshold})")

            self.push_stream.write(data)
            
            self._audio_debug_count += 1
            if self._audio_debug_count % 100 == 0:
                print(f"[azure_stt] Sent {self._audio_debug_count} audio chunks, latest RMS={energy}")
                
            if self._audio_debug_count == 1:
                print(f"[azure_stt] First chunk: {len(data)} bytes, RMS={energy}")
                
        except Exception as e:
            print(f"[azure_stt] Error sending audio: {e}")

    async def stop(self):
        try:
            self.speech_recognizer.stop_continuous_recognition_async().get()
            if self.push_stream:
                self.push_stream.close()
            print("[azure_stt] Stopped")
        except Exception as e:
            print(f"[azure_stt] Error stopping: {e}")