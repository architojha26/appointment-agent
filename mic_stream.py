import sounddevice as sd

class MicStream:
    def __init__(self, samplerate=8000, blocksize=320, channels=1, dtype='int16'):
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.channels = channels
        self.dtype = dtype

    def stream(self):
        with sd.RawInputStream(samplerate=self.samplerate, blocksize=self.blocksize, channels=self.channels, dtype=self.dtype) as in_stream:
            while True:
                data, _ = in_stream.read(self.blocksize)
                yield bytes(data)