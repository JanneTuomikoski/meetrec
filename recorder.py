"""
recorder.py – Audio capture module for MeetRec
Handles mic + system loopback recording with auto sample-rate detection.
"""

from pydub import AudioSegment
from scipy.signal import resample_poly
import soundcard as sc
import soundfile as sf
import numpy as np
import threading
import datetime
import os

CHUNK_DURATION = 0.5   # seconds per read chunk (larger = more stable)
OUTPUT_RATE    = 16000  # 16k mono is ideal for AssemblyAI transcription


def get_mic_samplerate(device):
    """Probe mic – try 16000 first as many headset mics are native 16k."""
    for rate in [16000, 48000, 44100, 32000, 8000]:
        try:
            with device.recorder(samplerate=rate, channels=1) as r:
                r.record(numframes=1)
            return rate
        except Exception:
            continue
    return 48000


def get_sys_samplerate(device):
    """Probe system loopback device."""
    for rate in [48000, 44100, 16000, 32000, 8000]:
        try:
            with device.recorder(samplerate=rate, channels=2) as r:
                r.record(numframes=1)
            return rate
        except Exception:
            continue
    return 48000


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio
    g = np.gcd(dst_rate, src_rate)
    return resample_poly(audio, dst_rate // g, src_rate // g)


class Recorder:
    def __init__(self, output_dir: str):
        self.output_dir  = output_dir
        self._stop_event = threading.Event()
        self._mic_chunks: list  = []
        self._sys_chunks: list  = []
        self._mic_rate   = OUTPUT_RATE
        self._sys_rate   = 48000
        self._mp3_path   = None

    def start(self):
        """Begin recording. Blocks until stop() is called."""
        os.makedirs(self.output_dir, exist_ok=True)
        self._stop_event.clear()
        self._mic_chunks = []
        self._sys_chunks = []

        mic      = sc.default_microphone()
        loopback = sc.get_microphone(sc.default_speaker().name, include_loopback=True)

        self._mic_rate = get_mic_samplerate(mic)
        self._sys_rate = get_sys_samplerate(loopback)

        def record_mic():
            with mic.recorder(samplerate=self._mic_rate, channels=1) as rec:
                while not self._stop_event.is_set():
                    data = rec.record(numframes=int(self._mic_rate * CHUNK_DURATION))
                    self._mic_chunks.append(data)

        def record_sys():
            with loopback.recorder(samplerate=self._sys_rate, channels=2) as rec:
                while not self._stop_event.is_set():
                    data = rec.record(numframes=int(self._sys_rate * CHUNK_DURATION))
                    self._sys_chunks.append(data)

        mic_t = threading.Thread(target=record_mic, daemon=True)
        sys_t = threading.Thread(target=record_sys,  daemon=True)
        mic_t.start()
        sys_t.start()
        mic_t.join()
        sys_t.join()

    def stop(self) -> str | None:
        """Stop recording, mix audio, save MP3. Returns path to MP3 or None."""
        self._stop_event.set()

        if not self._mic_chunks or not self._sys_chunks:
            return None

        mic_audio = np.concatenate(self._mic_chunks, axis=0)
        sys_audio = np.concatenate(self._sys_chunks, axis=0)

        # To mono
        mic_mono = mic_audio[:, 0] if mic_audio.ndim > 1 else mic_audio
        sys_mono = sys_audio.mean(axis=1) if sys_audio.ndim > 1 else sys_audio

        # Resample both to OUTPUT_RATE
        mic_rs = _resample(mic_mono, self._mic_rate, OUTPUT_RATE)
        sys_rs = _resample(sys_mono, self._sys_rate, OUTPUT_RATE)

        # Mix
        min_len = min(len(mic_rs), len(sys_rs))
        mixed   = (mic_rs[:min_len] * 1.2) + (sys_rs[:min_len] * 0.8)

        # Normalise
        peak = np.max(np.abs(mixed))
        if peak > 0:
            mixed = mixed / peak * 0.95

        # Save
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        temp_wav  = os.path.join(self.output_dir, f"meeting_{timestamp}_temp.wav")
        mp3_path  = os.path.join(self.output_dir, f"meeting_{timestamp}.mp3")

        sf.write(temp_wav, mixed, OUTPUT_RATE)
        AudioSegment.from_wav(temp_wav).export(mp3_path, format="mp3", bitrate="128k")
        os.remove(temp_wav)

        self._mp3_path = mp3_path
        return mp3_path
