"""
MeetRec - System tray meeting recorder
Run this file to start the tray app.
"""

import threading
import time
import os
import subprocess
from pathlib import Path

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

from recorder import Recorder
from transcriber import process_meeting

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
AUDIO_DIR       = BASE_DIR / "records" / "audio"
TRANSCRIPT_DIR  = BASE_DIR / "records" / "transcripts"
NOTES_DIR       = BASE_DIR / "records" / "notes"

for d in (AUDIO_DIR, TRANSCRIPT_DIR, NOTES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── State ──────────────────────────────────────────────────────────────────
recorder     = Recorder(str(AUDIO_DIR))
_tray_icon   = None
_status      = "idle"   # idle | recording | processing
_start_time  = None
_last_mp3    = None
_stop_tooltip = threading.Event()


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
            _tray_icon.title = "MeetRec – Idle"
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW
    )


# ── Recording actions ──────────────────────────────────────────────────────

def start_recording(icon, _item):
    global _status, _start_time
    if _status != "idle":
        return
    _status = "recording"
    _start_time = time.time()
    icon.icon = make_icon("recording")
    icon.update_menu()
    threading.Thread(target=recorder.start, daemon=True).start()


def stop_recording(icon, _item):
    global _status, _last_mp3
    if _status != "recording":
        return
    _status = "idle"
    icon.icon = make_icon("idle")
    mp3 = recorder.stop()
    _last_mp3 = mp3
    icon.update_menu()
    if mp3:
        notify("MeetRec", "Recording saved.\nTranscribe now?")
    else:
        notify("MeetRec", "Recording failed — no audio captured.")


def transcribe_last(icon, _item):
    global _status
    if not _last_mp3 or _status != "idle":
        return
    _status = "processing"
    icon.icon = make_icon("processing")
    icon.update_menu()

    def _run():
        global _status
        try:
            notes_file = process_meeting(
                _last_mp3,
                transcript_dir=str(TRANSCRIPT_DIR),
                notes_dir=str(NOTES_DIR),
            )
            notify("MeetRec", "✅ Notes ready!")
            if notes_file and os.path.exists(notes_file):
                os.startfile(notes_file)
        except Exception as e:
            notify("MeetRec", f"❌ Processing failed: {e}")
        finally:
            _status = "idle"
            icon.icon = make_icon("idle")
            icon.update_menu()

    threading.Thread(target=_run, daemon=True).start()


def open_folder(path):
    def _open(icon, _item):
        os.startfile(str(path))
    return _open


def on_quit(icon, _item):
    _stop_tooltip.set()
    if _status == "recording":
        recorder.stop()
    icon.stop()


# ── Menu ───────────────────────────────────────────────────────────────────

def build_menu():
    return pystray.Menu(
        item("▶  Start Recording",         start_recording, enabled=lambda _: _status == "idle"),
        item("⏹  Stop Recording",          stop_recording,  enabled=lambda _: _status == "recording"),
        pystray.Menu.SEPARATOR,
        item("📝  Transcribe & Notes",      transcribe_last,
             enabled=lambda _: _status == "idle" and bool(_last_mp3)),
        pystray.Menu.SEPARATOR,
        item("📁  Open Audio folder",       open_folder(AUDIO_DIR)),
        item("📄  Open Transcripts folder", open_folder(TRANSCRIPT_DIR)),
        item("🗒️  Open Notes folder",       open_folder(NOTES_DIR)),
        pystray.Menu.SEPARATOR,
        item("Quit", on_quit),
    )


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    global _tray_icon
    _tray_icon = pystray.Icon(
        "meetrec",
        make_icon("idle"),
        "MeetRec – Idle",
        menu=build_menu(),
    )
    _tray_icon.run()


if __name__ == "__main__":
    main()
