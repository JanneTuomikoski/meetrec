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
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtCore import QObject, Signal, Qt

from recorder import Recorder, list_microphones, list_speakers
from transcriber import process_meeting, generate_notes, RealtimeTranscriptionSession
from context_dialog import ask_meeting_context, ask_notes_conflict
from live_transcript_window import LiveTranscriptBridge, LiveTranscriptWindow
from level_meter_window import LevelBridge, LevelMeterWindow
from logger import log
from filenames import obsidian_filename
from notes import build_notes
from settings_store import apply_settings, settings_payload

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
_level_bridge          = None   # LevelBridge (created in main())
_level_window          = None   # LevelMeterWindow | None
_status                = "idle"   # idle | recording | processing
_start_time            = None
_last_mp3              = None
_auto_transcribe       = False
_realtime_mode         = False
_obsidian_vault: str | None = None
_realtime_session      = None   # RealtimeTranscriptionSession | None
_partial_transcript_path: str | None = None
_stop_tooltip          = threading.Event()


# ── Settings persistence ───────────────────────────────────────────────────

def _load_settings():
    global _auto_transcribe, _realtime_mode, _obsidian_vault
    try:
        if SETTINGS_FILE.exists():
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            _auto_transcribe, _realtime_mode, _obsidian_vault = apply_settings(s, recorder)
            log.info("Settings loaded")
    except Exception as e:
        log.error(f"Failed to load settings: {e}")


def _save_settings():
    try:
        payload = settings_payload(_auto_transcribe, _realtime_mode, recorder, _obsidian_vault)
        SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to save settings: {e}")


# ── Obsidian export ────────────────────────────────────────────────────────

def _obsidian_filename(mp3_stem: str, context) -> str:
    """Build a smart Obsidian filename from the recording stem and meeting context."""
    return obsidian_filename(mp3_stem, context)


def _copy_to_obsidian(notes_file: str, mp3_stem: str, context) -> "Path | None":
    global _obsidian_vault
    if not _obsidian_vault or not os.path.isdir(_obsidian_vault):
        return None
    try:
        filename = _obsidian_filename(mp3_stem, context)
        dest = Path(_obsidian_vault) / filename
        # Avoid silently overwriting: append counter if needed
        if dest.exists():
            base, n = dest.stem, 2
            while dest.exists():
                dest = Path(_obsidian_vault) / f"{base} ({n}).md"
                n += 1
        shutil.copy2(notes_file, dest)
        log.info(f"Notes copied to Obsidian: {dest}")
        return dest
    except Exception as e:
        log.error(f"Failed to copy to Obsidian: {e}")
        return None


def _open_in_obsidian(dest: "Path"):
    """Open a file inside the Obsidian vault using the obsidian:// URI scheme."""
    vault_name = Path(_obsidian_vault).name
    # Relative path inside the vault, without .md extension
    rel = dest.relative_to(_obsidian_vault).with_suffix("")
    uri = f"obsidian://open?vault={quote(vault_name)}&file={quote(str(rel).replace(os.sep, '/'))}"
    log.info(f"Opening Obsidian URI: {uri}")
    os.startfile(uri)


# ── Helpers ────────────────────────────────────────────────────────────────

def _recording_count() -> int:
    return sum(len(list(AUDIO_DIR.glob(f"*.{ext}"))) for ext in ("mp3", "flac"))


def _recent_recordings(n: int = 5) -> list[Path]:
    files = list(AUDIO_DIR.glob("*.mp3")) + list(AUDIO_DIR.glob("*.flac"))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:n]


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
    safe_title = title.replace("'", "''")
    safe_msg   = message.replace("'", "''").replace('\n', ' ')
    ps = f"""
    Add-Type -AssemblyName System.Windows.Forms
    $n = New-Object System.Windows.Forms.NotifyIcon
    $n.Icon = [System.Drawing.SystemIcons]::Information
    $n.Visible = $true
    $n.ShowBalloonTip(4000, '{safe_title}', '{safe_msg}', [System.Windows.Forms.ToolTipIcon]::None)
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

    _request_file_pick       = Signal()
    _request_vault_pick      = Signal()
    _request_context         = Signal(str)        # mp3 path
    _request_context_rt      = Signal(str, str)   # mp3 path, realtime transcript
    _request_transcript_pick = Signal()
    _request_notes_from_tx   = Signal(str)        # transcript path
    _open_live_window        = Signal()
    _close_live_window       = Signal()
    _open_level_window       = Signal()
    _close_level_window      = Signal()
    _do_quit                 = Signal()

    def __init__(self):
        super().__init__()
        self._request_file_pick.connect(self._do_file_pick)
        self._request_vault_pick.connect(self._do_vault_pick)
        self._request_context.connect(self._do_show_context)
        self._request_context_rt.connect(self._do_show_context_rt)
        self._request_transcript_pick.connect(self._do_transcript_pick)
        self._request_notes_from_tx.connect(self._do_notes_from_transcript)
        self._open_live_window.connect(self._do_open_live_window)
        self._close_live_window.connect(self._do_close_live_window)
        self._open_level_window.connect(self._do_open_level_window)
        self._close_level_window.connect(self._do_close_level_window)
        self._do_quit.connect(QApplication.instance().quit)

    def request_quit(self):
        self._do_quit.emit()

    # Called from any thread — safely
    def open_file_and_transcribe(self):
        self._request_file_pick.emit()

    def open_transcript_and_notes(self):
        self._request_transcript_pick.emit()

    def redo_notes_from_transcript(self, transcript_path: str):
        self._request_notes_from_tx.emit(transcript_path)

    def pick_obsidian_vault(self):
        self._request_vault_pick.emit()

    def show_context_and_transcribe(self, mp3_path: str):
        self._request_context.emit(mp3_path)

    def show_context_and_transcribe_rt(self, mp3_path: str, realtime_transcript: str):
        self._request_context_rt.emit(mp3_path, realtime_transcript)

    def open_live_window(self):
        self._open_live_window.emit()

    def close_live_window(self):
        self._close_live_window.emit()

    def open_level_window(self):
        self._open_level_window.emit()

    def close_level_window(self):
        self._close_level_window.emit()

    # ── Runs on main thread ──

    def _do_open_live_window(self):
        global _live_window
        _live_window = LiveTranscriptWindow()
        try:
            _live_bridge._update_partial.disconnect()
            _live_bridge._commit_final.disconnect()
            _live_bridge._close_window.disconnect()
        except RuntimeError:
            pass
        _live_bridge._update_partial.connect(_live_window.on_partial)
        _live_bridge._commit_final.connect(_live_window.on_final)
        _live_bridge._close_window.connect(_live_window.close_window)

    def _do_close_live_window(self):
        global _live_window
        if _live_window:
            _live_window.close_window()
            _live_window = None

    def _do_open_level_window(self):
        global _level_window
        _level_window = LevelMeterWindow()
        try:
            _level_bridge._mic_level.disconnect()
            _level_bridge._sys_level.disconnect()
            _level_bridge._close_window.disconnect()
        except RuntimeError:
            pass
        _level_bridge._mic_level.connect(_level_window.set_mic)
        _level_bridge._sys_level.connect(_level_window.set_sys)
        _level_bridge._close_window.connect(_level_window.close_window)

    def _do_close_level_window(self):
        global _level_window
        if _level_window:
            _level_window.close_window()
            _level_window = None

    def _do_file_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select a recording to transcribe",
            str(AUDIO_DIR),
            "Audio files (*.mp3 *.flac);;All files (*.*)",
        )
        if path:
            self._do_show_context(path)

    def _do_transcript_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select a transcript to regenerate notes from",
            str(TRANSCRIPT_DIR),
            "Transcript files (*.txt);;All files (*.*)",
        )
        if path:
            self._do_notes_from_transcript(path)

    def _do_notes_from_transcript(self, transcript_path: str):
        self._run_notes_flow(transcript_path)

    def _run_notes_flow(self, transcript_path: str):
        global _status
        stem = Path(transcript_path).stem.replace("_transcript", "").replace("meeting_", "").replace("_", " ")
        context = ask_meeting_context(default_title=stem)

        if context is None:
            log.info("Notes regeneration cancelled by user.")
            return

        notes_file = NOTES_DIR / f"{Path(transcript_path).stem.replace('_transcript', '')}_notes.md"
        notes_mode = "overwrite"
        existing_notes = ""
        if notes_file.exists():
            notes_mode = ask_notes_conflict(str(notes_file))
            if notes_mode is None:
                log.info("Notes conflict cancelled by user.")
                return
            if notes_mode in ("append", "improve"):
                existing_notes = notes_file.read_text(encoding="utf-8")

        _status = "processing"
        if _tray_icon:
            _tray_icon.icon = make_icon("processing")
            _tray_icon.update_menu()

        threading.Thread(
            target=_pipeline_notes_only,
            args=(transcript_path, str(notes_file), context, notes_mode, existing_notes),
            daemon=True,
        ).start()

    def _do_vault_pick(self):
        global _obsidian_vault
        start = _obsidian_vault or str(Path.home())
        folder = QFileDialog.getExistingDirectory(None, "Select Obsidian vault folder", start)
        if folder:
            _obsidian_vault = folder
            _save_settings()
            log.info(f"Obsidian vault set to: {folder}")
            if _tray_icon:
                _tray_icon.update_menu()

    def _do_show_context(self, mp3_path: str):
        self._run_context_flow(mp3_path)

    def _do_show_context_rt(self, mp3_path: str, realtime_transcript: str):
        self._run_context_flow(mp3_path, realtime_transcript)

    def _run_context_flow(self, mp3_path: str, realtime_transcript: str | None = None):
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

        if realtime_transcript is not None:
            threading.Thread(
                target=_pipeline_rt,
                args=(mp3_path, realtime_transcript, context, notes_mode, existing_notes),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=_pipeline, args=(mp3_path, context, notes_mode, existing_notes), daemon=True
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
        obsidian_dest = None
        if notes_file and os.path.exists(notes_file):
            obsidian_dest = _copy_to_obsidian(notes_file, Path(mp3_path).stem, context)
        notify("MeetRec", "✅ Notes ready!")
        if obsidian_dest:
            _open_in_obsidian(obsidian_dest)
        elif notes_file and os.path.exists(notes_file):
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

        notes = build_notes(
            realtime_transcript,
            context=context,
            notes_mode=notes_mode,
            existing_notes=existing_notes,
            notes_generator=generate_notes,
        )

        with open(notes_file, "w", encoding="utf-8") as f:
            f.write(notes)
        log.info(f"Notes saved: {notes_file}")

        obsidian_dest = _copy_to_obsidian(notes_file, stem, context)

        # Clean up partial transcript backup now that notes are saved
        if _partial_transcript_path and os.path.exists(_partial_transcript_path):
            try:
                os.remove(_partial_transcript_path)
                log.info(f"Partial transcript backup removed: {_partial_transcript_path}")
            except Exception:
                pass
        _partial_transcript_path = None

        notify("MeetRec", "✅ Notes ready!")
        if obsidian_dest:
            _open_in_obsidian(obsidian_dest)
        elif os.path.exists(notes_file):
            os.startfile(notes_file)
    except Exception as e:
        log.error(f"Realtime pipeline failed: {e}")
        notify("MeetRec", f"❌ Processing failed: {e}")
    finally:
        _status = "idle"
        if _tray_icon:
            _tray_icon.icon = make_icon("idle")
            _tray_icon.update_menu()


def _pipeline_notes_only(transcript_path: str, notes_file: str, context,
                         notes_mode: str = "overwrite", existing_notes: str = ""):
    global _status
    try:
        raw_text = Path(transcript_path).read_text(encoding="utf-8")
        log.info(f"Regenerating notes from transcript: {transcript_path}")

        notes = build_notes(
            raw_text,
            context=context,
            notes_mode=notes_mode,
            existing_notes=existing_notes,
            notes_generator=generate_notes,
        )

        with open(notes_file, "w", encoding="utf-8") as f:
            f.write(notes)
        log.info(f"Notes saved: {notes_file}")

        stem = Path(notes_file).stem.replace("_notes", "")
        obsidian_dest = _copy_to_obsidian(notes_file, stem, context)

        notify("MeetRec", "✅ Notes ready!")
        if obsidian_dest:
            _open_in_obsidian(obsidian_dest)
        elif os.path.exists(notes_file):
            os.startfile(notes_file)
    except Exception as e:
        log.error(f"Notes-only pipeline failed: {e}")
        notify("MeetRec", f"❌ Notes failed: {e}")
    finally:
        _status = "idle"
        if _tray_icon:
            _tray_icon.icon = make_icon("idle")
            _tray_icon.update_menu()


# ── Recording actions ──────────────────────────────────────────────────────

def _handle_recording_start_failed(message: str):
    global _status, _realtime_session
    _status = "idle"
    recorder.on_audio_chunk = None
    recorder.on_mic_level = None
    recorder.on_sys_level = None
    recorder.on_start_failed = None

    if _bridge:
        _bridge.close_level_window()
        _bridge.close_live_window()

    if _realtime_session:
        try:
            _realtime_session.stop()
        except Exception as e:
            log.error(f"Failed to stop realtime session after recorder startup failure: {e}")
        _realtime_session = None

    if _tray_icon:
        _tray_icon.icon = make_icon("idle")
        _tray_icon.update_menu()

    notify("MeetRec", message)


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
        threading.Thread(target=_realtime_session.start, daemon=True).start()
        recorder.on_audio_chunk = _realtime_session.send_chunk
        _bridge.open_live_window()
    else:
        recorder.on_audio_chunk = None

    recorder.on_mic_level = _level_bridge.update_mic
    recorder.on_sys_level = _level_bridge.update_sys
    recorder.on_start_failed = _handle_recording_start_failed
    _bridge.open_level_window()

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
    recorder.on_start_failed = None

    recorder.on_mic_level = None
    recorder.on_sys_level = None
    _bridge.close_level_window()

    realtime_transcript = None
    realtime_fallback = False
    if _realtime_session:
        _bridge.close_live_window()
        realtime_transcript = _realtime_session.stop()
        _realtime_session = None
        if not realtime_transcript.strip():
            realtime_fallback = True
            realtime_transcript = None
            log.warning("Realtime transcript was empty; falling back to batch transcription.")

    icon.update_menu()

    if not mp3:
        notify("MeetRec", "Recording failed — no audio captured.")
        return

    if realtime_transcript is not None:
        _status = "processing"
        icon.icon = make_icon("processing")
        icon.update_menu()
        _bridge.show_context_and_transcribe_rt(mp3, realtime_transcript)
    elif _auto_transcribe or realtime_fallback:
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


def redo_notes_from_file(icon, _item):
    if _status != "idle":
        return
    _bridge.open_transcript_and_notes()


def redo_notes_specific(transcript_path: str):
    def _do(icon, _item):
        if _status != "idle":
            return
        _bridge.redo_notes_from_transcript(transcript_path)
    return _do


def _recent_transcripts(n: int = 5) -> list[Path]:
    files = list(TRANSCRIPT_DIR.glob("*_transcript.txt"))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:n]


def recent_transcripts_submenu():
    recent = _recent_transcripts(5)
    if not recent:
        return pystray.Menu(
            item("  No transcripts yet", lambda *_: None, enabled=lambda _: False)
        )
    entries = []
    for p in recent:
        name = p.stem.replace("_transcript", "").replace("meeting_", "")
        parts = name.split("_")
        if len(parts) >= 2:
            label = f"{parts[0]}  {parts[1].replace('-', ':')[:5]}"
        else:
            label = p.stem
        entries.append(item(f"  {label}", redo_notes_specific(str(p))))
    return pystray.Menu(*entries)


def set_obsidian_vault(icon, _item):
    _bridge.pick_obsidian_vault()


def open_obsidian_vault(icon, _item):
    if _obsidian_vault and os.path.isdir(_obsidian_vault):
        os.startfile(_obsidian_vault)


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


# ── Boost selection ───────────────────────────────────────────────────────

_BOOST_LEVELS = [0.5, 0.8, 1.0, 1.3, 1.5, 2.0]


def set_mic_boost(level):
    def _set(icon, _item):
        recorder.mic_boost = level
        log.info(f"Mic boost set to: {level}x")
        _save_settings()
        icon.update_menu()
    return _set


def set_sys_boost(level):
    def _set(icon, _item):
        recorder.sys_boost = level
        log.info(f"System boost set to: {level}x")
        _save_settings()
        icon.update_menu()
    return _set


def mic_boost_submenu():
    return pystray.Menu(*[
        item(
            f"{'✔  ' if recorder.mic_boost == lvl else '     '}{lvl}x",
            set_mic_boost(lvl),
        )
        for lvl in _BOOST_LEVELS
    ])


def sys_boost_submenu():
    return pystray.Menu(*[
        item(
            f"{'✔  ' if recorder.sys_boost == lvl else '     '}{lvl}x",
            set_sys_boost(lvl),
        )
        for lvl in _BOOST_LEVELS
    ])


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
    icon.stop()     # Remove from tray immediately so the UI feels responsive

    def _shutdown():
        if _status == "recording":
            recorder.stop()     # May block while ffmpeg encodes — fine in background
        _bridge.request_quit()  # Dispatches to Qt main thread via signal

    threading.Thread(target=_shutdown, daemon=True).start()


# ── Menu ───────────────────────────────────────────────────────────────────

def folders_submenu():
    return pystray.Menu(
        item("📁  Audio folder",       open_folder(AUDIO_DIR)),
        item("📄  Transcripts folder", open_folder(TRANSCRIPT_DIR)),
        item("🗒️  Notes folder",       open_folder(NOTES_DIR)),
    )


def obsidian_submenu():
    return pystray.Menu(
        item("Open vault", open_obsidian_vault,
             enabled=lambda _: bool(_obsidian_vault and os.path.isdir(_obsidian_vault))),
        item("Set vault…", set_obsidian_vault),
    )


def settings_submenu():
    return pystray.Menu(
        item(lambda _: f"🔁  Auto-transcribe  {'✔' if _auto_transcribe else ''}",
             toggle_auto_transcribe),
        item(lambda _: f"⚡  Live transcription  {'✔' if _realtime_mode else ''}",
             toggle_realtime_mode),
        pystray.Menu.SEPARATOR,
        item("🎙️  Microphone", pystray.Menu(mic_submenu)),
        item("🔊  Loopback",   pystray.Menu(spk_submenu)),
        item(lambda _: f"🎙️  Mic boost  {recorder.mic_boost}x",  pystray.Menu(mic_boost_submenu)),
        item(lambda _: f"🔊  Sys boost  {recorder.sys_boost}x",  pystray.Menu(sys_boost_submenu)),
        pystray.Menu.SEPARATOR,
        item(lambda _: f"📗  Obsidian vault  {'✔' if _obsidian_vault and os.path.isdir(_obsidian_vault) else ''}",
             pystray.Menu(obsidian_submenu)),
        pystray.Menu.SEPARATOR,
        item("📁  Folders",  pystray.Menu(folders_submenu)),
        item("📋  Open log", lambda _i, _: os.startfile(str(BASE_DIR / "records" / "meetrec.log"))),
    )


def build_menu():
    return pystray.Menu(
        item("▶  Start Recording", start_recording, enabled=lambda _: _status == "idle"),
        item("⏹  Stop Recording",  stop_recording,  enabled=lambda _: _status == "recording"),
        pystray.Menu.SEPARATOR,
        item("📝  Transcribe & Notes", transcribe_last,
             enabled=lambda _: _status == "idle" and bool(_last_mp3)),
        item("📂  Transcribe a file…", transcribe_file,
             enabled=lambda _: _status == "idle"),
        item("📼  Recent recordings",  pystray.Menu(recordings_submenu),
             enabled=lambda _: _status == "idle"),
        pystray.Menu.SEPARATOR,
        item("🔄  Redo notes from transcript…", redo_notes_from_file,
             enabled=lambda _: _status == "idle"),
        item("📋  Recent transcripts", pystray.Menu(recent_transcripts_submenu),
             enabled=lambda _: _status == "idle"),
        pystray.Menu.SEPARATOR,
        item("⚙️  Settings", pystray.Menu(settings_submenu)),
        pystray.Menu.SEPARATOR,
        item("Quit", on_quit),
    )


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    global _tray_icon, _bridge, _live_bridge, _level_bridge

    # Qt must own the main thread
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Force non-native dialogs to avoid COM threading conflicts with pystray
    app.setAttribute(Qt.AA_DontUseNativeDialogs, True)

    _live_bridge = LiveTranscriptBridge()
    _level_bridge = LevelBridge()
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
