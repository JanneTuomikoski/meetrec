"""Pure audio mixing helpers for MeetRec."""

import numpy as np


def mix_mono_tracks(
    mic_audio: np.ndarray | None,
    sys_audio: np.ndarray | None,
    mic_boost: float,
    sys_boost: float,
) -> np.ndarray | None:
    """Mix optional mono tracks, preserving the longer track with silence padding."""
    if mic_audio is None and sys_audio is None:
        return None

    mic_len = len(mic_audio) if mic_audio is not None else 0
    sys_len = len(sys_audio) if sys_audio is not None else 0
    out_len = max(mic_len, sys_len)
    mixed = np.zeros(out_len, dtype=np.float32)

    if mic_audio is not None and mic_len:
        mixed[:mic_len] += mic_audio.astype(np.float32, copy=False) * mic_boost
    if sys_audio is not None and sys_len:
        mixed[:sys_len] += sys_audio.astype(np.float32, copy=False) * sys_boost

    peak = np.max(np.abs(mixed)) if out_len else 0
    if peak > 0:
        mixed = mixed / peak * 0.95
    return mixed
