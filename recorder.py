"""
recorder.py – Audio capture module for MeetRec.
Handles mic + system loopback recording with auto sample-rate detection
and optional device selection.
"""

from pydub import AudioSegment
from scipy.signal import resample_poly
import soundcard as sc
import soundfile as sf
import numpy as np
import threading
import datetime
import queue
import os

from logger import log

CHUNK_DURATION = 0.5    # seconds per read chunk
OUTPUT_RATE    = 16000  # 16k mono is ideal for AssemblyAI


def list_microphones() -> list[str]:
    return [m.name for m in sc.all_microphones(include_loopback=False)]


def list_speakers() -> list[str]:
    return [s.name for s in sc.all_speakers()]


def get_mic_samplerate(device) -> int:
    for rate in [16000, 48000, 44100, 32000, 8000]:
        try:
            with device.recorder(samplerate=rate, channels=1) as r:
                r.record(numframes=1)
            return rate
        except Exception:
            continue
    return 48000


def get_sys_samplerate(device) -> int:
    for rate in [48000, 44100, 16000, 32000, 8000]:
        try:
            with device.recorder(samplerate=rate, channels=2) as r:
                r.record(numframes=1)
            return rate
        except Exception:
            continue
    return 48000


def _resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return audio
    g = np.gcd(dst, src)
    return resample_poly(audio, dst // g, src // g)


class Recorder:
    def __init__(self, output_dir: str):
        self.output_dir    = output_dir
        self.mic_name      = None   # None = use default
        self.speaker_name  = None   # None = use default
        self.on_audio_chunk = None  # Callable[[bytes], None] for real-time streaming
        self._stop_event   = threading.Event()
        self._mic_chunks: list = []
        self._sys_chunks: list = []
        self._mic_rate     = OUTPUT_RATE
        self._sys_rate     = 48000
        self._mix_thread   = None

    def start(self):
        """Begin recording. Blocks until stop() is called."""
        os.makedirs(self.output_dir, exist_ok=True)
        self._stop_event.clear()
        self._mic_chunks = []
        self._sys_chunks = []

        try:
            mic = (sc.get_microphone(self.mic_name, include_loopback=False)
                   if self.mic_name else sc.default_microphone())
            spk_name = self.speaker_name or sc.default_speaker().name
            loopback = sc.get_microphone(spk_name, include_loopback=True)
        except Exception as e:
            log.error(f"Failed to open audio devices: {e}")
            return

        self._mic_rate = get_mic_samplerate(mic)
        self._sys_rate = get_sys_samplerate(loopback)
        log.info(f"Recording started | mic={mic.name} @ {self._mic_rate}Hz | loopback={loopback.name} @ {self._sys_rate}Hz")

        streaming = self.on_audio_chunk is not None
        mic_q = queue.Queue() if streaming else None
        sys_q = queue.Queue() if streaming else None

        def record_mic():
            try:
                with mic.recorder(samplerate=self._mic_rate, channels=1) as rec:
                    while not self._stop_event.is_set():
                        data = rec.record(numframes=int(self._mic_rate * CHUNK_DURATION))
                        self._mic_chunks.append(data)
                        if streaming:
                            mono = data[:, 0] if data.ndim > 1 else data
                            mic_q.put(_resample(mono, self._mic_rate, OUTPUT_RATE))
            except Exception as e:
                log.error(f"Mic recording error: {e}")

        def record_sys():
            try:
                with loopback.recorder(samplerate=self._sys_rate, channels=2) as rec:
                    while not self._stop_event.is_set():
                        data = rec.record(numframes=int(self._sys_rate * CHUNK_DURATION))
                        self._sys_chunks.append(data)
                        if streaming:
                            mono = data.mean(axis=1) if data.ndim > 1 else data
                            sys_q.put(_resample(mono, self._sys_rate, OUTPUT_RATE))
            except Exception as e:
                log.error(f"System audio recording error: {e}")

        def mix_and_stream():
            while not self._stop_event.is_set() or not mic_q.empty():
                try:
                    mic_c = mic_q.get(timeout=0.6)
                except queue.Empty:
                    continue
                try:
                    sys_c = sys_q.get(timeout=0.1)
                except queue.Empty:
                    sys_c = np.zeros_like(mic_c)
                min_len = min(len(mic_c), len(sys_c))
                mixed = (mic_c[:min_len] * 1.3) + (sys_c[:min_len] * 0.8)
                peak = np.max(np.abs(mixed))
                if peak > 0:
                    mixed = mixed / peak * 0.95
                try:
                    self.on_audio_chunk((mixed * 32767).astype(np.int16).tobytes())
                except Exception as e:
                    log.error(f"Chunk callback error: {e}")

        mic_t = threading.Thread(target=record_mic, daemon=True)
        sys_t = threading.Thread(target=record_sys,  daemon=True)
        mic_t.start()
        sys_t.start()

        if streaming:
            self._mix_thread = threading.Thread(target=mix_and_stream, daemon=True)
            self._mix_thread.start()

        mic_t.join()
        sys_t.join()

        if self._mix_thread:
            self._mix_thread.join(timeout=3.0)
            self._mix_thread = None

    def stop(self) -> str | None:
        """Stop recording, mix and save MP3. Returns path or None."""
        self._stop_event.set()

        if not self._mic_chunks or not self._sys_chunks:
            log.warning("Recording stopped but no audio was captured.")
            return None

        try:
            mic_audio = np.concatenate(self._mic_chunks, axis=0)
            sys_audio = np.concatenate(self._sys_chunks, axis=0)

            mic_mono = mic_audio[:, 0] if mic_audio.ndim > 1 else mic_audio
            sys_mono = sys_audio.mean(axis=1) if sys_audio.ndim > 1 else sys_audio

            mic_rs = _resample(mic_mono, self._mic_rate, OUTPUT_RATE)
            sys_rs = _resample(sys_mono, self._sys_rate, OUTPUT_RATE)

            min_len = min(len(mic_rs), len(sys_rs))
            mixed   = (mic_rs[:min_len] * 1.3) + (sys_rs[:min_len] * 0.8)

            peak = np.max(np.abs(mixed))
            if peak > 0:
                mixed = mixed / peak * 0.95

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            temp_wav  = os.path.join(self.output_dir, f"meeting_{timestamp}_temp.wav")
            mp3_path  = os.path.join(self.output_dir, f"meeting_{timestamp}.mp3")

            sf.write(temp_wav, mixed, OUTPUT_RATE)
            AudioSegment.from_wav(temp_wav).export(mp3_path, format="mp3", bitrate="128k")
            os.remove(temp_wav)

            log.info(f"Recording saved: {mp3_path}")
            return mp3_path

        except Exception as e:
            log.error(f"Failed to save recording: {e}")
            return None
