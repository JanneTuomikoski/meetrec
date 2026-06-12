"""Pure audio mixing helpers for MeetRec."""

import numpy as np


def stereo_tracks(
    mic_audio: np.ndarray | None,
    sys_audio: np.ndarray | None,
    mic_boost: float,
    sys_boost: float,
) -> np.ndarray | None:
    """Combine optional mono tracks into stereo: mic on the left channel,
    system audio on the right, padding the shorter track with silence.

    Keeping the sources on separate channels lets transcription attribute
    the mic channel to the recording owner with certainty."""
    if mic_audio is None and sys_audio is None:
        return None

    mic_len = len(mic_audio) if mic_audio is not None else 0
    sys_len = len(sys_audio) if sys_audio is not None else 0
    out_len = max(mic_len, sys_len)
    stereo = np.zeros((out_len, 2), dtype=np.float32)

    if mic_audio is not None and mic_len:
        stereo[:mic_len, 0] = mic_audio.astype(np.float32, copy=False) * mic_boost
    if sys_audio is not None and sys_len:
        stereo[:sys_len, 1] = sys_audio.astype(np.float32, copy=False) * sys_boost

    # Normalize both channels with the same gain to preserve relative levels
    peak = np.max(np.abs(stereo)) if out_len else 0
    if peak > 0:
        stereo = stereo / peak * 0.95
    return stereo
