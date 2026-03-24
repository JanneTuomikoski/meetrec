"""
MeetRec - System tray meeting recorder
Run with: pythonw tray.py
"""

import threading
import time
import os
import subprocess
from pathlib import Path
from PySide6.QtWidgets import QApplication, QFileDialog

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

from recorder import Recorder, list_microphones, list_speakers
from transcriber import process_meeting
from context_dialog import ask_meeting_context
from logger import log

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
AUDIO_DIR      = BASE_DIR / "records" / "audio"
TRANSCRIPT_DIR = BASE_DIR / "records" / "transcripts"
NOTES_DIR      = BASE_DIR / "records" / "notes"

for d in (AUDIO_DIR, TRANSCRIPT_DIR, NOTES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── State ──────────────────────────────────────────────────────────────────
recorder       = Recorder(str(AUDIO_DIR))
_tray_icon     = None
_status        = "idle"   # idle | recording | processing
_start_time    = None
_last_mp3      = None
_auto_transcribe = False
_stop_tooltip  = threading.Event()


# ── Helpers ────────────────────────────────────────────────────────────────

def _recording_count() -> int:
    return len(list(AUDIO_DIR.glob("*.mp3")))


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


# ── Transcribe pipeline (shared) ───────────────────────────────────────────

def _run_transcribe(mp3_path: str, icon):
    """Show context dialog, then run transcription. Called from a thread."""
    global _status

    # Derive a default title from filename timestamp
    stem = Path(mp3_path).stem.replace("meeting_", "").replace("_", " ")
    context = ask_meeting_context(default_title=stem)

    if context is None:
        # User clicked Cancel — abort
        log.info("Transcription cancelled by user.")
        _status = "idle"
        icon.icon = make_icon("idle")
        icon.update_menu()
        return

    try:
        notes_file = process_meeting(
            mp3_path,
            transcript_dir=str(TRANSCRIPT_DIR),
            notes_dir=str(NOTES_DIR),
            context=context,
        )
        notify("MeetRec", "✅ Notes ready!")
        if notes_file and os.path.exists(notes_file):
            os.startfile(notes_file)
    except Exception as e:
        log.error(f"Transcription pipeline failed: {e}")
        notify("MeetRec", f"❌ Processing failed: {e}")
    finally:
        _status = "idle"
        icon.icon = make_icon("idle")
        icon.update_menu()


# ── Recording actions ──────────────────────────────────────────────────────

def start_recording(icon, _item):
    global _status, _start_time
    if _status != "idle":
        return
    _status = "recording"
    _start_time = time.time()
    icon.icon = make_icon("recording")
    icon.update_menu()
    log.info("Recording started")
    threading.Thread(target=recorder.start, daemon=True).start()


def stop_recording(icon, _item):
    global _status, _last_mp3
    if _status != "recording":
        return
    log.info("Recording stopped")
    _status = "idle"
    icon.icon = make_icon("idle")
    mp3 = recorder.stop()
    _last_mp3 = mp3
    icon.update_menu()

    if not mp3:
        notify("MeetRec", "Recording failed — no audio captured.")
        return

    if _auto_transcribe:
        _status = "processing"
        icon.icon = make_icon("processing")
        icon.update_menu()
        threading.Thread(target=_run_transcribe, args=(mp3, icon), daemon=True).start()
    else:
        notify("MeetRec", "Recording saved.\nUse menu to transcribe.")


def transcribe_last(icon, _item):
    global _status
    if not _last_mp3 or _status != "idle":
        return
    _status = "processing"
    icon.icon = make_icon("processing")
    icon.update_menu()
    threading.Thread(target=_run_transcribe, args=(_last_mp3, icon), daemon=True).start()


def transcribe_file(icon, _item):
    """Open a file picker and transcribe any MP3."""
    global _status
    if _status != "idle":
        return

    app = QApplication.instance() or QApplication([])
    path, _ = QFileDialog.getOpenFileName(
        None,
        "Select a recording to transcribe",
        str(AUDIO_DIR),
        "MP3 files (*.mp3);;All files (*.*)",
    )

    if not path:
        return

    _status = "processing"
    icon.icon = make_icon("processing")
    icon.update_menu()
    threading.Thread(target=_run_transcribe, args=(path, icon), daemon=True).start()


def toggle_auto_transcribe(icon, _item):
    global _auto_transcribe
    _auto_transcribe = not _auto_transcribe
    log.info(f"Auto-transcribe: {'on' if _auto_transcribe else 'off'}")
    icon.update_menu()


# ── Device selection ───────────────────────────────────────────────────────

def set_mic(name):
    def _set(icon, _item):
        recorder.mic_name = name
        log.info(f"Microphone set to: {name}")
        icon.update_menu()
    return _set


def set_speaker(name):
    def _set(icon, _item):
        recorder.speaker_name = name
        log.info(f"Speaker/loopback set to: {name}")
        icon.update_menu()
    return _set


def mic_submenu():
    mics = list_microphones()
    return pystray.Menu(*[
        item(
            f"{'✔  ' if recorder.mic_name == m or (recorder.mic_name is None and i == 0) else '     '}{m}",
            set_mic(m),
        )
        for i, m in enumerate(mics)
    ])


def spk_submenu():
    spks = list_speakers()
    return pystray.Menu(*[
        item(
            f"{'✔  ' if recorder.speaker_name == s or (recorder.speaker_name is None and i == 0) else '     '}{s}",
            set_speaker(s),
        )
        for i, s in enumerate(spks)
    ])


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


# ── Menu ───────────────────────────────────────────────────────────────────

def build_menu():
    return pystray.Menu(
        item("▶  Start Recording",   start_recording, enabled=lambda _: _status == "idle"),
        item("⏹  Stop Recording",    stop_recording,  enabled=lambda _: _status == "recording"),
        pystray.Menu.SEPARATOR,
        item("📝  Transcribe & Notes",  transcribe_last,
             enabled=lambda _: _status == "idle" and bool(_last_mp3)),
        item("📂  Transcribe a file…",  transcribe_file,
             enabled=lambda _: _status == "idle"),
        item(lambda _: f"🔁  Auto-transcribe  {'✔' if _auto_transcribe else ''}",
             toggle_auto_transcribe),
        pystray.Menu.SEPARATOR,
        item("🎙️  Microphone",  pystray.Menu(mic_submenu)),
        item("🔊  Loopback",    pystray.Menu(spk_submenu)),
        pystray.Menu.SEPARATOR,
        item("📁  Audio folder",       open_folder(AUDIO_DIR)),
        item("📄  Transcripts folder", open_folder(TRANSCRIPT_DIR)),
        item("🗒️  Notes folder",       open_folder(NOTES_DIR)),
        item("📋  Open log",           lambda i, _: os.startfile(str(BASE_DIR / "records" / "meetrec.log"))),
        pystray.Menu.SEPARATOR,
        item("Quit", on_quit),
    )


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    global _tray_icon
    log.info("MeetRec started")
    _tray_icon = pystray.Icon(
        "meetrec",
        make_icon("idle"),
        "MeetRec – Idle",
        menu=build_menu(),
    )
    _tray_icon.run()


if __name__ == "__main__":
    main()
