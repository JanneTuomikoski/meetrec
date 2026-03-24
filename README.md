# MeetRec 🎙️

System tray meeting recorder with automatic transcription and AI-generated notes.

## Setup

1. **Install dependencies**
   ```
   pip install pystray pillow python-dotenv pydub soundcard soundfile scipy assemblyai anthropic
   pip install git+https://github.com/bastibe/SoundCard.git  # use git version, not PyPI
   ```

2. **Configure API keys**
   ```
   copy .env.example .env
   ```
   Then edit `.env` and fill in your keys:
   ```
   ASSEMBLYAI_API_KEY=your_assemblyai_key_here
   ANTHROPIC_API_KEY=your_anthropic_key_here
   ```

3. **Run**
   ```
   pythonw tray.py
   ```
   Use `pythonw` instead of `python` to run without a console window.

## Usage

Right-click the tray icon (bottom-right taskbar) to:

- **▶ Start Recording** – begins capturing mic + system audio
- **⏹ Stop Recording** – stops and saves the MP3
- **📝 Transcribe & Notes** – sends to AssemblyAI + Claude, opens notes when done
- **📁 Open Records Folder** – opens the `records/` folder in Explorer

The tray icon changes colour:
- 🟢 Green = idle
- 🔴 Red = recording (tooltip shows duration)
- 🟡 Yellow = processing transcript

## Files

```
meetrec/
├── tray.py          # Main entry point – run this
├── recorder.py      # Audio capture logic
├── transcriber.py   # AssemblyAI + Claude pipeline
├── .env             # Your API keys (never commit this)
├── .env.example     # Template
└── records/         # All recordings, transcripts and notes saved here
```

## Auto-start with Windows

To have MeetRec start with Windows:
1. Press `Win+R`, type `shell:startup`
2. Create a shortcut to `tray.py` there
3. In the shortcut properties, set "Target" to: `pythonw.exe C:\tools\meetrec\tray.py`
