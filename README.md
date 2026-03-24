# MeetRec 🎙️

System tray meeting recorder with automatic transcription and AI-generated notes.

## Setup

1. **Install dependencies**
   ```
   pip install pystray pillow python-dotenv pydub soundfile scipy assemblyai anthropic
   pip install git+https://github.com/bastibe/SoundCard.git
   ```

2. **Configure API keys**
   ```
   copy .env.example .env
   ```
   Edit `.env`:
   ```
   ASSEMBLYAI_API_KEY=your_key_here
   ANTHROPIC_API_KEY=your_key_here
   ```

3. **Run**
   ```
   pythonw tray.py
   ```

## Files

```
meetrec/
├── tray.py            # Entry point – run this
├── recorder.py        # Audio capture
├── transcriber.py     # AssemblyAI + Claude pipeline
├── context_dialog.py  # Meeting details dialog
├── logger.py          # Logging setup
├── .env               # API keys (never commit)
├── .env.example       # Template
└── records/
    ├── audio/         # MP3 recordings
    ├── transcripts/   # _transcript.txt files
    ├── notes/         # _notes.md files
    └── meetrec.log    # Application log
```

## Tray menu

| Item | Description |
|------|-------------|
| ▶ Start / ⏹ Stop Recording | Toggle recording |
| 📝 Transcribe & Notes | Process last recording |
| 📂 Transcribe a file… | Pick any MP3 to transcribe |
| 🔁 Auto-transcribe | Automatically transcribe after every recording |
| 🎙️ Microphone | Select input device |
| 🔊 Loopback | Select system audio source |
| 📋 Open log | View meetrec.log |

## Auto-start with Windows

1. Press `Win+R` → `shell:startup`
2. Create a shortcut with target: `pythonw.exe C:\tools\meetrec\tray.py`
