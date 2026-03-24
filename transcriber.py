"""
transcriber.py – Transcription and notes module for MeetRec
Uses AssemblyAI for speaker-diarized transcription, Claude for notes.
API keys are loaded from .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

import assemblyai as aai
import anthropic

load_dotenv(Path(__file__).parent / ".env")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY",  "")


def transcribe_with_speakers(mp3_path: str) -> str:
    aai.settings.api_key = ASSEMBLYAI_API_KEY

    config = aai.TranscriptionConfig(
        speech_models=["universal-3-pro", "universal-2"],
        speaker_labels=True,
        language_detection=True,
    )

    transcript = aai.Transcriber().transcribe(mp3_path, config)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI error: {transcript.error}")

    if not transcript.utterances:
        raise RuntimeError("No utterances returned — transcription may have failed.")

    lines = []
    for utt in transcript.utterances:
        minutes = int(utt.start / 60000)
        seconds = int((utt.start % 60000) / 1000)
        lines.append(f"[{minutes:02d}:{seconds:02d}] Speaker {utt.speaker}: {utt.text}")

    return "\n".join(lines)


def generate_notes(raw_text: str) -> str:
    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = claude.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""You are an expert meeting note-taker. Analyze this meeting transcript and produce:

1. **Meeting Summary** – 3-5 sentence overview
2. **Key Discussion Points** – bullet list of main topics
3. **Decisions Made** – what was agreed upon
4. **Action Items / To-Do List** – who does what by when (if mentioned)
5. **Follow-up Questions** – anything left unresolved

Note: Speakers are labeled A, B, C etc. If you can infer names or roles from context, use them.

Transcript:
{raw_text}"""
        }]
    )
    return response.content[0].text


def process_meeting(mp3_path: str, transcript_dir: str, notes_dir: str) -> str | None:
    """Full pipeline: transcribe → save transcript → generate notes → save notes.
    Returns path to the notes .md file."""
    if not os.path.exists(mp3_path):
        raise FileNotFoundError(f"File not found: {mp3_path}")

    stem = Path(mp3_path).stem
    transcript_file = os.path.join(transcript_dir, f"{stem}_transcript.txt")
    notes_file      = os.path.join(notes_dir,      f"{stem}_notes.md")

    raw_text = transcribe_with_speakers(mp3_path)

    with open(transcript_file, "w", encoding="utf-8") as f:
        f.write(raw_text)

    notes = generate_notes(raw_text)

    with open(notes_file, "w", encoding="utf-8") as f:
        f.write(notes)

    return notes_file
