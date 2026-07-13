"""Background Vosk thread. Mic audio in, final transcripts + word timestamps out."""

import json
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model

import config


class WordEvent:
    def __init__(self, word, t_start, t_end):
        self.word = word
        self.t_start = t_start  # time.monotonic() epoch, comparable to pointer samples
        self.t_end = t_end


class FinalResult:
    def __init__(self, text, words):
        self.text = text
        self.words = words  # list[WordEvent]


class SpeechEngine:
    def __init__(self):
        self._model = Model(config.VOSK_MODEL_PATH)
        self._recognizer = KaldiRecognizer(self._model, config.SAMPLE_RATE)
        self._recognizer.SetWords(True)

        self._audio_q = queue.Queue()
        self._results_q = queue.Queue()
        self._stream = None
        self._worker_thread = None
        self._running = False
        self._stream_start_t = None

    def _audio_callback(self, indata, frames, time_info, status):
        if config.AUDIO_GAIN != 1.0:
            samples = np.frombuffer(bytes(indata), dtype=np.int16).astype(np.int32)
            samples = np.clip(samples * config.AUDIO_GAIN, -32768, 32767).astype(np.int16)
            self._audio_q.put(samples.tobytes())
        else:
            self._audio_q.put(bytes(indata))

    def start(self):
        self._running = True
        self._stream_start_t = time.monotonic()
        self._stream = sd.RawInputStream(
            samplerate=config.SAMPLE_RATE,
            blocksize=config.BLOCK_SIZE,
            dtype="int16",
            channels=1,
            callback=self._audio_callback,
        )
        self._stream.start()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def _worker(self):
        while self._running:
            try:
                data = self._audio_q.get(timeout=0.2)
            except queue.Empty:
                continue

            if self._recognizer.AcceptWaveform(data):
                result = json.loads(self._recognizer.Result())
                text = result.get("text", "")
                if not text:
                    continue
                words = [
                    WordEvent(
                        word=w["word"],
                        t_start=self._stream_start_t + w["start"],
                        t_end=self._stream_start_t + w["end"],
                    )
                    for w in result.get("result", [])
                ]
                self._results_q.put(FinalResult(text=text, words=words))

    def poll(self):
        """Non-blocking: returns list of FinalResult since last call."""
        results = []
        while True:
            try:
                results.append(self._results_q.get_nowait())
            except queue.Empty:
                break
        return results

    def stop(self):
        self._running = False
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=1.0)
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
