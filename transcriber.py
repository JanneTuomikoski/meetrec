"""
transcriber.py – Transcription and notes module for MeetRec.
Uses AssemblyAI for speaker-diarized transcription, Claude for notes.
API keys loaded from .env. Accepts optional MeetingContext for richer notes.
"""

import os
import threading
from pathlib import Path
from dotenv import load_dotenv

import assemblyai as aai
from assemblyai.streaming.v3 import (
    StreamingClient,
    StreamingClientOptions,
    StreamingParameters,
    SpeechModel,
    StreamingEvents,
)
import anthropic

from logger import log

load_dotenv(Path(__file__).parent / ".env")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY",  "")


def transcribe_with_speakers(mp3_path: str, language: str = "") -> str:
    aai.settings.api_key = ASSEMBLYAI_API_KEY
    log.info(f"Uploading to AssemblyAI: {mp3_path}")

    config_kwargs = dict(
        speech_models=["universal-3-pro", "universal-2"],
        speaker_labels=True,
    )
    if language:
        config_kwargs["language_code"] = language
        log.info(f"Language hint: {language}")
    else:
        config_kwargs["language_detection"] = True

    config    = aai.TranscriptionConfig(**config_kwargs)
    transcript = aai.Transcriber().transcribe(mp3_path, config)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI error: {transcript.error}")

    if not transcript.utterances:
        raise RuntimeError("No utterances returned — transcription may have failed.")

    log.info(f"Transcription complete: {len(transcript.utterances)} utterances")

    lines = []
    for utt in transcript.utterances:
        minutes = int(utt.start / 60000)
        seconds = int((utt.start % 60000) / 1000)
        lines.append(f"[{minutes:02d}:{seconds:02d}] Speaker {utt.speaker}: {utt.text}")

    return "\n".join(lines)


def generate_notes(raw_text: str, context=None) -> str:
    log.info("Generating notes with Claude")

    notes_lang = "Finnish" if (context and context.notes_language == "fi") else "English"

    context_block = ""
    if context:
        parts = []
        if context.title:
            parts.append(f"Meeting title: {context.title}")
        if context.participants:
            parts.append(f"Participants: {context.participants}")
        if context.agenda:
            parts.append(f"Topic / agenda: {context.agenda}")
        if parts:
            context_block = "Context provided:\n" + "\n".join(parts) + "\n\n"

    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = claude.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""You are an expert meeting note-taker. Write all notes in {notes_lang}. Analyze this meeting transcript and produce:

1. **Meeting Summary** – 3-5 sentence overview
2. **Key Discussion Points** – bullet list of main topics
3. **Decisions Made** – what was agreed upon
4. **Action Items / To-Do List** – who does what by when (if mentioned)
5. **Follow-up Questions** – anything left unresolved

{context_block}Speakers are labeled A, B, C etc. Use participant names from the context above wherever possible instead of Speaker A/B/C.

Transcript:
{raw_text}"""
        }]
    )

    notes = response.content[0].text
    log.info("Notes generated successfully")
    return notes


class RealtimeTranscriptionSession:
    """Streams PCM audio to AssemblyAI (v3) in real time and accumulates the transcript."""

    def __init__(self, on_partial=None, on_final=None):
        self.on_partial = on_partial   # Callable[[str], None]
        self.on_final   = on_final     # Callable[[str], None]
        self._finals: list[str] = []
        self._client  = None
        self._connected = threading.Event()

    def start(self, sample_rate: int = 16000):
        options = StreamingClientOptions(api_key=ASSEMBLYAI_API_KEY)
        self._client = StreamingClient(options=options)

        def _on_begin(_client, event):
            self._connected.set()
            log.info(f"Streaming session started: {event.id}")

        def _on_turn(_client, event):
            if not event.transcript:
                return
            if event.end_of_turn:
                # Skip if identical to the previous final (API occasionally re-emits)
                if not self._finals or self._finals[-1] != event.transcript:
                    self._finals.append(event.transcript)
                    if self.on_final:
                        self.on_final(event.transcript)
            else:
                if self.on_partial:
                    self.on_partial(event.transcript)

        def _on_error(_client, error):
            log.error(f"RealtimeTranscriber error: {error}")

        self._client.on(StreamingEvents.Begin, _on_begin)
        self._client.on(StreamingEvents.Turn,  _on_turn)
        self._client.on(StreamingEvents.Error, _on_error)

        params = StreamingParameters(
            sample_rate=sample_rate,
            speech_model=SpeechModel.u3_rt_pro,
        )
        self._client.connect(params=params)
        self._connected.wait(timeout=10.0)

    def send_chunk(self, pcm_bytes: bytes):
        if self._client and self._connected.is_set():
            self._client.stream(pcm_bytes)

    def stop(self) -> str:
        if self._client:
            self._client.disconnect(terminate=True)
            self._client = None
        result = " ".join(self._finals)
        log.info(f"Realtime transcription complete: {len(self._finals)} utterances")
        return result


def process_meeting(mp3_path: str, transcript_dir: str, notes_dir: str, context=None) -> str | None:
    """Full pipeline: transcribe → save transcript → generate notes → save notes.
    Returns path to the notes .md file."""

    if not os.path.exists(mp3_path):
        raise FileNotFoundError(f"File not found: {mp3_path}")

    log.info(f"Processing meeting: {mp3_path}")

    stem            = Path(mp3_path).stem
    transcript_file = os.path.join(transcript_dir, f"{stem}_transcript.txt")
    notes_file      = os.path.join(notes_dir,      f"{stem}_notes.md")

    language = context.language if context else ""
    raw_text = transcribe_with_speakers(mp3_path, language=language)

    with open(transcript_file, "w", encoding="utf-8") as f:
        f.write(raw_text)
    log.info(f"Transcript saved: {transcript_file}")

    notes = generate_notes(raw_text, context=context)

    # Prepend title if provided
    if context and context.title:
        notes = f"# {context.title}\n\n{notes}"

    with open(notes_file, "w", encoding="utf-8") as f:
        f.write(notes)
    log.info(f"Notes saved: {notes_file}")

    return notes_file
