"""
MeetRec - System tray meeting recorder
Run with: pythonw tray.py
"""
import pythoncom
pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)

import sys
import json
import threading
import time
import os
import subprocess
from pathlib import Path

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtCore import QObject, Signal, QTimer, Qt

from recorder import Recorder, list_microphones, list_speakers
from transcriber import process_meeting, generate_notes, RealtimeTranscriptionSession
from context_dialog import ask_meeting_context, ask_notes_conflict
from live_transcript_window import LiveTranscriptBridge, LiveTranscriptWindow
from logger import log

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
AUDIO_DIR      = BASE_DIR / "records" / "audio"
TRANSCRIPT_DIR = BASE_DIR / "records" / "transcripts"
NOTES_DIR      = BASE_DIR / "records" / "notes"
SETTINGS_FILE  = BASE_DIR / "records" / "settings.json"

for d in (AUDIO_DIR, TRANSCRIPT_DIR, NOTES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── State ──────────────────────────────────────────────────────────────────
recorder               = Recorder(str(AUDIO_DIR))
_tray_icon             = None
_bridge                = None
_live_bridge           = None   # LiveTranscriptBridge (created in main())
_live_window           = None   # LiveTranscriptWindow | None
_status                = "idle"   # idle | recording | processing
_start_time            = None
_last_mp3              = None
_auto_transcribe       = False
_realtime_mode         = False
_realtime_session      = None   # RealtimeTranscriptionSession | None
_partial_transcript_path: str | None = None
_stop_tooltip          = threading.Event()


# ── Settings persistence ───────────────────────────────────────────────────

def _load_settings():
    global _auto_transcribe, _realtime_mode
    try:
        if SETTINGS_FILE.exists():
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            _auto_transcribe      = s.get("auto_transcribe", False)
            _realtime_mode        = s.get("realtime_mode", False)
            recorder.mic_name     = s.get("mic_name")
            recorder.speaker_name = s.get("speaker_name")
            log.info("Settings loaded")
    except Exception as e:
        log.error(f"Failed to load settings: {e}")


def _save_settings():
    try:
        SETTINGS_FILE.write_text(json.dumps({
            "auto_transcribe":  _auto_transcribe,
            "realtime_mode":    _realtime_mode,
            "mic_name":         recorder.mic_name,
            "speaker_name":     recorder.speaker_name,
        }, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to save settings: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────

def _recording_count() -> int:
    return len(list(AUDIO_DIR.glob("*.mp3")))


def _recent_recordings(n: int = 5) -> list[Path]:
    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    return sorted(mp3s, key=lambda p: p.stat().st_mtime, reverse=True)[:n]


# ── Icon drawing ───────────────────────────────────────────────────────────

def make_icon(state: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = {"idle": (60, 180, 60), "recording": (220, 40, 40), "processing": (220, 180, 0)}
    color = colors.get(state, (100, 100, 100))
    draw.ellipse([8, 8, 56, 56], fill=color)
    draw.rectangle([28, 18, 36, 38], fill="white")
    draw.arc([22, 30, 42, 46], 0, 180, fill="white", width=3)
    draw.line([32, 46, 32, 54], fill="white", width=3)
    draw.line([24, 54, 40, 54], fill="white", width=3)
    return img


# ── Tooltip updater ────────────────────────────────────────────────────────

def _tooltip_loop():
    while not _stop_tooltip.is_set():
        if _tray_icon and _status == "recording" and _start_time:
            elapsed = time.time() - _start_time
            m, s = int(elapsed // 60), int(elapsed % 60)
            _tray_icon.title = f"🔴 Recording: {m:02d}:{s:02d}"
        elif _tray_icon and _status == "processing":
            _tray_icon.title = "⏳ Processing transcript..."
        elif _tray_icon:
            count = _recording_count()
            _tray_icon.title = f"MeetRec – Idle  ({count} recording{'s' if count != 1 else ''})"
        time.sleep(1)

threading.Thread(target=_tooltip_loop, daemon=True).start()


# ── Windows toast notification ─────────────────────────────────────────────

def notify(title: str, message: str):
    ps = f"""
    Add-Type -AssemblyName System.Windows.Forms
    $n = New-Object System.Windows.Forms.NotifyIcon
    $n.Icon = [System.Drawing.SystemIcons]::Information
    $n.Visible = $true
    $n.ShowBalloonTip(4000, '{title}', '{message}', [System.Windows.Forms.ToolTipIcon]::None)
    Start-Sleep -Milliseconds 4500
    $n.Dispose()
    """
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


# ── UI Bridge — all Qt dialogs must run on the main thread ─────────────────

class UIBridge(QObject):
    """Receives signals from tray/worker threads and runs Qt UI on main thread."""

    _request_file_pick   = Signal()
    _request_context     = Signal(str)        # mp3 path
    _request_context_rt  = Signal(str, str)   # mp3 path, realtime transcript
    _open_live_window    = Signal()
    _close_live_window   = Signal()

    def __init__(self):
        super().__init__()
        self._request_file_pick.connect(self._do_file_pick)
        self._request_context.connect(self._do_show_context)
        self._request_context_rt.connect(self._do_show_context_rt)
        self._open_live_window.connect(self._do_open_live_window)
        self._close_live_window.connect(self._do_close_live_window)

    # Called from any thread — safely
    def open_file_and_transcribe(self):
        self._request_file_pick.emit()

    def show_context_and_transcribe(self, mp3_path: str):
        self._request_context.emit(mp3_path)

    def show_context_and_transcribe_rt(self, mp3_path: str, realtime_transcript: str):
        self._request_context_rt.emit(mp3_path, realtime_transcript)

    def open_live_window(self):
        self._open_live_window.emit()

    def close_live_window(self):
        self._close_live_window.emit()

    # ── Runs on main thread ──

    def _do_open_live_window(self):
        global _live_window
        _live_window = LiveTranscriptWindow()
        _live_bridge._update_partial.connect(_live_window.on_partial)
        _live_bridge._commit_final.connect(_live_window.on_final)
        _live_bridge._close_window.connect(_live_window.close_window)

    def _do_close_live_window(self):
        global _live_window
        if _live_window:
            _live_window.close_window()
            _live_window = None

    def _do_file_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select a recording to transcribe",
            str(AUDIO_DIR),
            "MP3 files (*.mp3);;All files (*.*)",
        )
        if path:
            self._do_show_context(path)

    def _do_show_context(self, mp3_path: str):
        global _status
        stem    = Path(mp3_path).stem.replace("meeting_", "").replace("_", " ")
        context = ask_meeting_context(default_title=stem)

        if context is None:
            log.info("Transcription cancelled by user.")
            _status = "idle"
            if _tray_icon:
                _tray_icon.icon = make_icon("idle")
                _tray_icon.update_menu()
            return

        # Check for existing notes and ask what to do
        notes_file = NOTES_DIR / f"{Path(mp3_path).stem}_notes.md"
        notes_mode = "overwrite"
        existing_notes = ""
        if notes_file.exists():
            notes_mode = ask_notes_conflict(str(notes_file))
            if notes_mode is None:
                log.info("Notes conflict cancelled by user.")
                _status = "idle"
                if _tray_icon:
                    _tray_icon.icon = make_icon("idle")
                    _tray_icon.update_menu()
                return
            if notes_mode in ("append", "improve"):
                existing_notes = notes_file.read_text(encoding="utf-8")

        _status = "processing"
        if _tray_icon:
            _tray_icon.icon = make_icon("processing")
            _tray_icon.update_menu()

        threading.Thread(
            target=_pipeline, args=(mp3_path, context, notes_mode, existing_notes), daemon=True
        ).start()

    def _do_show_context_rt(self, mp3_path: str, realtime_transcript: str):
        global _status
        stem    = Path(mp3_path).stem.replace("meeting_", "").replace("_", " ")
        context = ask_meeting_context(default_title=stem)

        if context is None:
            log.info("Transcription cancelled by user.")
            _status = "idle"
            if _tray_icon:
                _tray_icon.icon = make_icon("idle")
                _tray_icon.update_menu()
            return

        # Check for existing notes and ask what to do
        notes_file = NOTES_DIR / f"{Path(mp3_path).stem}_notes.md"
        notes_mode = "overwrite"
        existing_notes = ""
        if notes_file.exists():
            notes_mode = ask_notes_conflict(str(notes_file))
            if notes_mode is None:
                log.info("Notes conflict cancelled by user.")
                _status = "idle"
                if _tray_icon:
                    _tray_icon.icon = make_icon("idle")
                    _tray_icon.update_menu()
                return
            if notes_mode in ("append", "improve"):
                existing_notes = notes_file.read_text(encoding="utf-8")

        _status = "processing"
        if _tray_icon:
            _tray_icon.icon = make_icon("processing")
            _tray_icon.update_menu()

        threading.Thread(
            target=_pipeline_rt,
            args=(mp3_path, realtime_transcript, context, notes_mode, existing_notes),
            daemon=True,
        ).start()


# ── Transcription pipeline (worker thread) ─────────────────────────────────

def _pipeline(mp3_path: str, context, notes_mode: str = "overwrite", existing_notes: str = ""):
    global _status
    try:
        notes_file = process_meeting(
            mp3_path,
            transcript_dir=str(TRANSCRIPT_DIR),
            notes_dir=str(NOTES_DIR),
            context=context,
            notes_mode=notes_mode,
            existing_notes=existing_notes,
        )
        notify("MeetRec", "✅ Notes ready!")
        if notes_file and os.path.exists(notes_file):
            os.startfile(notes_file)
    except Exception as e:
        log.error(f"Transcription pipeline failed: {e}")
        notify("MeetRec", f"❌ Processing failed: {e}")
    finally:
        _status = "idle"
        if _tray_icon:
            _tray_icon.icon = make_icon("idle")
            _tray_icon.update_menu()


def _pipeline_rt(mp3_path: str, realtime_transcript: str, context,
                 notes_mode: str = "overwrite", existing_notes: str = ""):
    global _status, _partial_transcript_path
    try:
        stem            = Path(mp3_path).stem
        transcript_file = str(TRANSCRIPT_DIR / f"{stem}_transcript.txt")
        notes_file      = str(NOTES_DIR / f"{stem}_notes.md")

        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write(realtime_transcript)
        log.info(f"Realtime transcript saved: {transcript_file}")

        if notes_mode == "append":
            notes = generate_notes(realtime_transcript, context=context)
            if context and context.title:
                notes = f"# {context.title}\n\n{notes}"
            if existing_notes:
                notes = existing_notes + "\n\n---\n\n" + notes
        elif notes_mode == "improve":
            notes = generate_notes(realtime_transcript, context=context, existing_notes=existing_notes)
            if context and context.title and not notes.startswith("#"):
                notes = f"# {context.title}\n\n{notes}"
        else:
            notes = generate_notes(realtime_transcript, context=context)
            if context and context.title:
                notes = f"# {context.title}\n\n{notes}"

        with open(notes_file, "w", encoding="utf-8") as f:
            f.write(notes)
        log.info(f"Notes saved: {notes_file}")

        # Clean up partial transcript backup now that notes are saved
        if _partial_transcript_path and os.path.exists(_partial_transcript_path):
            try:
                os.remove(_partial_transcript_path)
                log.info(f"Partial transcript backup removed: {_partial_transcript_path}")
            except Exception:
                pass
        _partial_transcript_path = None

        notify("MeetRec", "✅ Notes ready!")
        if os.path.exists(notes_file):
            os.startfile(notes_file)
    except Exception as e:
        log.error(f"Realtime pipeline failed: {e}")
        notify("MeetRec", f"❌ Processing failed: {e}")
    finally:
        _status = "idle"
        if _tray_icon:
            _tray_icon.icon = make_icon("idle")
            _tray_icon.update_menu()


# ── Recording actions ──────────────────────────────────────────────────────

def start_recording(icon, _item):
    global _status, _start_time, _realtime_session, _partial_transcript_path
    if _status != "idle":
        return
    _status = "recording"
    _start_time = time.time()
    icon.icon = make_icon("recording")
    icon.update_menu()
    log.info("Recording started")

    if _realtime_mode:
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        _partial_transcript_path = str(TRANSCRIPT_DIR / f"partial_{timestamp}.txt")

        def _on_final_with_save(text: str):
            _live_bridge.commit_final(text)
            try:
                with open(_partial_transcript_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception as e:
                log.error(f"Failed to write partial transcript: {e}")

        _realtime_session = RealtimeTranscriptionSession(
            on_partial=_live_bridge.update_partial,
            on_final=_on_final_with_save,
        )
        _realtime_session.start()
        recorder.on_audio_chunk = _realtime_session.send_chunk
        _bridge.open_live_window()
    else:
        recorder.on_audio_chunk = None

    threading.Thread(target=recorder.start, daemon=True).start()


def stop_recording(icon, _item):
    global _status, _last_mp3, _realtime_session
    if _status != "recording":
        return
    log.info("Recording stopped")
    _status = "idle"
    icon.icon = make_icon("idle")
    mp3 = recorder.stop()
    _last_mp3 = mp3

    realtime_transcript = None
    if _realtime_session:
        _bridge.close_live_window()
        realtime_transcript = _realtime_session.stop()
        _realtime_session = None

    icon.update_menu()

    if not mp3:
        notify("MeetRec", "Recording failed — no audio captured.")
        return

    if realtime_transcript is not None:
        _status = "processing"
        icon.icon = make_icon("processing")
        icon.update_menu()
        _bridge.show_context_and_transcribe_rt(mp3, realtime_transcript)
    elif _auto_transcribe:
        _status = "processing"
        icon.icon = make_icon("processing")
        icon.update_menu()
        _bridge.show_context_and_transcribe(mp3)
    else:
        notify("MeetRec", "Recording saved.\nUse menu to transcribe.")


def transcribe_last(icon, _item):
    global _status
    if not _last_mp3 or _status != "idle":
        return
    _status = "processing"
    icon.icon = make_icon("processing")
    icon.update_menu()
    _bridge.show_context_and_transcribe(_last_mp3)


def transcribe_file(icon, _item):
    if _status != "idle":
        return
    _bridge.open_file_and_transcribe()


def transcribe_specific(mp3_path: str):
    def _do(icon, _item):
        global _status
        if _status != "idle":
            return
        _status = "processing"
        icon.icon = make_icon("processing")
        icon.update_menu()
        _bridge.show_context_and_transcribe(mp3_path)
    return _do


def toggle_auto_transcribe(icon, _item):
    global _auto_transcribe
    _auto_transcribe = not _auto_transcribe
    log.info(f"Auto-transcribe: {'on' if _auto_transcribe else 'off'}")
    _save_settings()
    icon.update_menu()


def toggle_realtime_mode(icon, _item):
    global _realtime_mode
    _realtime_mode = not _realtime_mode
    log.info(f"Live transcription: {'on' if _realtime_mode else 'off'}")
    _save_settings()
    icon.update_menu()


# ── Device selection ───────────────────────────────────────────────────────

def set_mic(name):
    def _set(icon, _item):
        recorder.mic_name = name
        log.info(f"Microphone set to: {name}")
        _save_settings()
        icon.update_menu()
    return _set


def set_mic_default(icon, _item):
    recorder.mic_name = None
    log.info("Microphone set to: Windows default")
    _save_settings()
    icon.update_menu()


def set_speaker(name):
    def _set(icon, _item):
        recorder.speaker_name = name
        log.info(f"Speaker/loopback set to: {name}")
        _save_settings()
        icon.update_menu()
    return _set


def set_speaker_default(icon, _item):
    recorder.speaker_name = None
    log.info("Speaker/loopback set to: Windows default")
    _save_settings()
    icon.update_menu()


def mic_submenu():
    mics = list_microphones()
    return pystray.Menu(
        item(
            f"{'✔  ' if recorder.mic_name is None else '     '}Windows Default",
            set_mic_default,
        ),
        pystray.Menu.SEPARATOR,
        *[
            item(
                f"{'✔  ' if recorder.mic_name == m else '     '}{m}",
                set_mic(m),
            )
            for m in mics
        ],
    )


def spk_submenu():
    spks = list_speakers()
    return pystray.Menu(
        item(
            f"{'✔  ' if recorder.speaker_name is None else '     '}Windows Default",
            set_speaker_default,
        ),
        pystray.Menu.SEPARATOR,
        *[
            item(
                f"{'✔  ' if recorder.speaker_name == s else '     '}{s}",
                set_speaker(s),
            )
            for s in spks
        ],
    )


# ── Recordings submenu ─────────────────────────────────────────────────────

def recordings_submenu():
    recent = _recent_recordings(5)
    if not recent:
        return pystray.Menu(
            item("  No recordings yet", lambda *_: None, enabled=lambda _: False)
        )
    entries = []
    for p in recent:
        # Parse filename: meeting_YYYY-MM-DD_HH-MM-SS.mp3
        name = p.stem.replace("meeting_", "")
        parts = name.split("_")
        if len(parts) >= 2:
            label = f"{parts[0]}  {parts[1].replace('-', ':')[:5]}"
        else:
            label = p.stem
        entries.append(item(f"  {label}", transcribe_specific(str(p))))
    return pystray.Menu(*entries)


# ── Folder helpers ─────────────────────────────────────────────────────────

def open_folder(path):
    def _open(icon, _item):
        os.startfile(str(path))
    return _open


# ── Quit ──────────────────────────────────────────────────────────────────

def on_quit(icon, _item):
    log.info("MeetRec quit")
    _stop_tooltip.set()
    if _status == "recording":
        recorder.stop()
    icon.stop()
    QApplication.instance().quit()


# ── Menu ───────────────────────────────────────────────────────────────────

def build_menu():
    return pystray.Menu(
        item("▶  Start Recording",      start_recording, enabled=lambda _: _status == "idle"),
        item("⏹  Stop Recording",       stop_recording,  enabled=lambda _: _status == "recording"),
        pystray.Menu.SEPARATOR,
        item("📝  Transcribe & Notes",   transcribe_last,
             enabled=lambda _: _status == "idle" and bool(_last_mp3)),
        item("📂  Transcribe a file…",   transcribe_file,
             enabled=lambda _: _status == "idle"),
        item("📼  Recent recordings",    pystray.Menu(recordings_submenu),
             enabled=lambda _: _status == "idle"),
        item(lambda _: f"🔁  Auto-transcribe  {'✔' if _auto_transcribe else ''}",
             toggle_auto_transcribe),
        item(lambda _: f"⚡  Live transcription  {'✔' if _realtime_mode else ''}",
             toggle_realtime_mode),
        pystray.Menu.SEPARATOR,
        item("🎙️  Microphone",  pystray.Menu(mic_submenu)),
        item("🔊  Loopback",    pystray.Menu(spk_submenu)),
        pystray.Menu.SEPARATOR,
        item("📁  Audio folder",        open_folder(AUDIO_DIR)),
        item("📄  Transcripts folder",  open_folder(TRANSCRIPT_DIR)),
        item("🗒️  Notes folder",        open_folder(NOTES_DIR)),
        item("📋  Open log",            lambda i, _: os.startfile(str(BASE_DIR / "records" / "meetrec.log"))),
        pystray.Menu.SEPARATOR,
        item("Quit", on_quit),
    )


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    global _tray_icon, _bridge, _live_bridge

    # Qt must own the main thread
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Force non-native dialogs to avoid COM threading conflicts with pystray
    app.setAttribute(Qt.AA_DontUseNativeDialogs, True)

    _live_bridge = LiveTranscriptBridge()
    _bridge = UIBridge()

    _load_settings()

    log.info("MeetRec started")
    _tray_icon = pystray.Icon(
        "meetrec",
        make_icon("idle"),
        "MeetRec – Idle",
        menu=build_menu(),
    )

    # pystray runs in a background thread; Qt event loop owns main thread
    threading.Thread(target=_tray_icon.run, daemon=True).start()

    app.exec()


if __name__ == "__main__":
    main()
