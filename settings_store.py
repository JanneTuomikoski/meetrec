"""Settings serialization helpers for MeetRec."""

DEFAULT_AUTO_TRANSCRIBE = False
DEFAULT_REALTIME_MODE = False
DEFAULT_MIC_BOOST = 1.3
DEFAULT_SYS_BOOST = 0.8


def settings_payload(auto_transcribe, realtime_mode, recorder, obsidian_vault):
    return {
        "auto_transcribe": auto_transcribe,
        "realtime_mode": realtime_mode,
        "mic_name": recorder.mic_name,
        "speaker_name": recorder.speaker_name,
        "mic_boost": recorder.mic_boost,
        "sys_boost": recorder.sys_boost,
        "obsidian_vault": obsidian_vault,
    }


def apply_settings(settings, recorder):
    recorder.mic_name = settings.get("mic_name")
    recorder.speaker_name = settings.get("speaker_name")
    recorder.mic_boost = settings.get("mic_boost", DEFAULT_MIC_BOOST)
    recorder.sys_boost = settings.get("sys_boost", DEFAULT_SYS_BOOST)
    return (
        settings.get("auto_transcribe", DEFAULT_AUTO_TRANSCRIBE),
        settings.get("realtime_mode", DEFAULT_REALTIME_MODE),
        settings.get("obsidian_vault"),
    )
