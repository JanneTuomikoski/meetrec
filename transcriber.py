"""
transcriber.py – Transcription and notes module for MeetRec.
Uses AssemblyAI for speaker-diarized transcription, Claude for notes.
API keys loaded from .env. Accepts optional MeetingContext for richer notes.
"""

import os
import time
import threading
from collections import deque
from pathlib import Path
from dotenv import load_dotenv

import assemblyai as aai
import soundfile as sf
from assemblyai.streaming.v3 import (
    StreamingClient,
    StreamingClientOptions,
    StreamingParameters,
    SpeechModel,
    StreamingEvents,
)
import anthropic

from logger import log
from notes import build_notes

load_dotenv(Path(__file__).parent / ".env")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY",  "")

MEETING_PROMPTS = {
    "general": """Analyze this meeting transcript and produce:

1. **Meeting Summary** – 3-5 sentence overview
2. **Key Discussion Points** – bullet list of main topics
3. **Decisions Made** – what was agreed upon
4. **Action Items / To-Do List** – who does what by when (if mentioned)
5. **Follow-up Questions** – anything left unresolved""",

    "standup": """Analyze this standup transcript and produce a concise summary:

1. **Updates per person** – what each person accomplished since last standup
2. **Plans** – what each person will work on next
3. **Blockers** – anything blocking progress or needing team attention""",

    "one_on_one": """Analyze this 1:1 meeting transcript and produce:

1. **Summary** – 2-3 sentence overview of the conversation
2. **Topics Discussed** – main subjects covered
3. **Feedback Given / Received** – any feedback exchanged
4. **Goals & Development** – career or project goals mentioned
5. **Action Items** – concrete next steps for each person""",

    "brainstorm": """Analyze this brainstorming session transcript and produce:

1. **Summary** – what problem or topic was being explored
2. **Ideas Generated** – all ideas mentioned, grouped by theme if possible
3. **Top Candidates** – ideas that received the most enthusiasm or discussion
4. **Rejected / Parked Ideas** – ideas set aside and why (if mentioned)
5. **Next Steps** – any decisions to pursue specific ideas""",

    "interview": """Analyze this interview transcript and produce:

1. **Candidate / Role** – who was interviewed and for what position (if mentioned)
2. **Key Strengths** – positive signals from the interview
3. **Concerns / Gaps** – areas of uncertainty or weakness noted
4. **Notable Responses** – standout answers (good or bad)
5. **Recommendation** – hire / no hire / follow-up (if discussed)
6. **Next Steps** – what happens next in the process""",

    "client": """Analyze this client call transcript and produce:

1. **Summary** – 2-3 sentence overview
2. **Client Needs & Requests** – what the client asked for or raised
3. **Commitments Made** – what was promised to the client
4. **Issues / Concerns Raised** – problems or risks mentioned
5. **Action Items** – follow-up tasks with owners (if mentioned)
6. **Next Steps** – agreed timeline or next meeting""",
}


def _channel_count(audio_path: str) -> int:
    try:
        return sf.info(audio_path).channels
    except Exception as e:
        log.warning(f"Could not probe channel count ({e}); assuming mono")
        return 1


def transcribe_with_speakers(mp3_path: str, language: str = "", mic_speaker: str = "") -> str:
    aai.settings.api_key = ASSEMBLYAI_API_KEY
    log.info(f"Uploading to AssemblyAI: {mp3_path}")

    # Recordings made by MeetRec are stereo with the mic on channel 1 and
    # system audio on channel 2, so the mic channel identifies the local
    # speaker with certainty. Mono files (older recordings, external audio)
    # fall back to plain diarization.
    multichannel = _channel_count(mp3_path) >= 2

    config_kwargs = dict(
        speech_models=["universal-3-pro", "universal-2"],
        speaker_labels=True,
    )
    if multichannel:
        config_kwargs["multichannel"] = True
        log.info(f"Multichannel transcription | mic channel = {mic_speaker or 'unnamed'}")
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
    for utt in sorted(transcript.utterances, key=lambda u: u.start):
        minutes = int(utt.start / 60000)
        seconds = int((utt.start % 60000) / 1000)
        if multichannel and str(getattr(utt, "channel", "")) == "1":
            label = mic_speaker or "Me (mic)"
        else:
            label = f"Speaker {utt.speaker}"
        lines.append(f"[{minutes:02d}:{seconds:02d}] {label}: {utt.text}")

    return "\n".join(lines)


def generate_notes(raw_text: str, context=None, existing_notes: str = "") -> str:
    log.info("Generating notes with Claude")

    notes_lang = "Finnish" if (context and context.notes_language == "fi") else "English"

    context_block = ""
    if context:
        parts = []
        if context.title:
            parts.append(f"Meeting title: {context.title}")
        participants = context.participants
        if getattr(context, "me_present", False) and getattr(context, "my_name", ""):
            participants = f"{context.my_name}, {participants}" if participants else context.my_name
        if participants:
            parts.append(f"Participants: {participants}")
        if context.agenda:
            parts.append(f"Topic / agenda: {context.agenda}")
        if parts:
            context_block = "Context provided:\n" + "\n".join(parts) + "\n\n"

    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if existing_notes:
        prompt = f"""You are an expert meeting note-taker. Write all notes in {notes_lang}.

Here are the existing notes previously generated for this recording:

{existing_notes}

Here is the complete meeting transcript:

{context_block}Transcript:
{raw_text}

Please improve and refine the existing notes based on the full transcript. Fix any inaccuracies, fill in missing information, clarify unclear points, and ensure all action items and decisions are captured. Maintain the same structure and format."""
    else:
        meeting_type = (context.meeting_type if context and hasattr(context, "meeting_type") else None) or "general"
        structure = MEETING_PROMPTS.get(meeting_type, MEETING_PROMPTS["general"])
        mic_speaker = getattr(context, "mic_speaker", "") if context else ""
        if mic_speaker:
            speaker_note = (
                f"Speakers already named in the transcript (e.g. {mic_speaker}) were identified "
                f"from their own microphone channel — that attribution is certain, never reassign "
                f"those lines. Speakers labeled A, B, C were detected automatically; map them to "
                f"the remaining participant names from the context above when confident, otherwise "
                f"keep the generic label rather than guessing."
            )
        else:
            speaker_note = (
                "Speakers are labeled A, B, C etc. Use participant names from the context above "
                "wherever possible instead of Speaker A/B/C; if you cannot confidently match a "
                "speaker to a name, keep the generic label rather than guessing."
            )
        prompt = f"""You are an expert meeting note-taker. Write all notes in {notes_lang}. {structure}

{context_block}{speaker_note}

Transcript:
{raw_text}"""

    response = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}]
    )

    if not response.content:
        raise RuntimeError("Claude returned an empty response — no notes generated.")
    notes = response.content[0].text
    if response.stop_reason == "max_tokens":
        log.warning("Notes hit the max_tokens limit and were truncated")
        notes += "\n\n> ⚠️ **Note:** these notes were cut off at the model's output limit and may be incomplete."
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
        self._sample_rate = 16000
        self._stop_requested = False
        self.had_error = False
        self._pending_chunks = deque(maxlen=120)

    def _on_begin(self, _client, event):
        self._connected.set()
        log.info(f"Streaming session started: {event.id}")

    def _on_turn(self, _client, event):
        if not event.transcript:
            return
        if event.end_of_turn:
            if not self._finals or self._finals[-1] != event.transcript:
                self._finals.append(event.transcript)
                if self.on_final:
                    self.on_final(event.transcript)
        else:
            if self.on_partial:
                self.on_partial(event.transcript)

    def _on_error(self, _client, error):
        self.had_error = True
        log.error(f"RealtimeTranscriber error: {error}")
        if not self._stop_requested:
            threading.Thread(target=self._reconnect, daemon=True).start()

    def _reconnect(self):
        log.info("Streaming error — attempting reconnect in 2s...")
        time.sleep(2)
        if self._stop_requested:
            return
        try:
            self._connected.clear()
            self._do_connect()
            if self._stop_requested:
                if self._client:
                    self._client.disconnect(terminate=True)
                    self._client = None
                return
            log.info("Reconnected to streaming API")
        except Exception as e:
            log.error(f"Reconnect failed: {e}")

    def _do_connect(self):
        options = StreamingClientOptions(api_key=ASSEMBLYAI_API_KEY)
        self._client = StreamingClient(options=options)
        self._client.on(StreamingEvents.Begin, self._on_begin)
        self._client.on(StreamingEvents.Turn,  self._on_turn)
        self._client.on(StreamingEvents.Error, self._on_error)
        params = StreamingParameters(
            sample_rate=self._sample_rate,
            speech_model=SpeechModel.u3_rt_pro,
        )
        self._client.connect(params=params)
        if not self._connected.wait(timeout=10.0):
            self.had_error = True
            log.error("Streaming connection timed out after 10s")

    def start(self, sample_rate: int = 16000):
        self._sample_rate = sample_rate
        self._stop_requested = False
        try:
            self._do_connect()
        except Exception as e:
            self.had_error = True
            log.error(f"RealtimeTranscriber start failed: {e}")
            return
        if self._stop_requested and self._client:
            self._client.disconnect(terminate=True)
            self._client = None

    def send_chunk(self, pcm_bytes: bytes):
        if not self._client or not self._connected.is_set():
            self._pending_chunks.append(pcm_bytes)
            return
        try:
            while self._pending_chunks:
                self._client.stream(self._pending_chunks.popleft())
            self._client.stream(pcm_bytes)
        except AttributeError:
            pass  # _client cleared by stop() during this call — not an error
        except Exception as e:
            self.had_error = True
            log.error(f"RealtimeTranscriber stream failed: {e}")

    def stop(self) -> str:
        self._stop_requested = True
        if self._client:
            self._client.disconnect(terminate=True)
            self._client = None
        # The server flushes remaining audio as a final turn on terminate;
        # that event can still be in flight when disconnect returns. Wait
        # until no new finals arrive (max 2s) before joining the transcript.
        deadline = time.time() + 2.0
        last_count = -1
        while time.time() < deadline and len(self._finals) != last_count:
            last_count = len(self._finals)
            time.sleep(0.25)
        result = " ".join(self._finals)
        self._pending_chunks.clear()
        log.info(f"Realtime transcription complete: {len(self._finals)} utterances")
        return result


def process_meeting(mp3_path: str, transcript_dir: str, notes_dir: str, context=None,
                    notes_mode: str = "overwrite", existing_notes: str = "") -> str | None:
    """Full pipeline: transcribe → save transcript → generate/update notes → save notes.
    notes_mode: 'overwrite' (default), 'append', or 'improve'.
    Returns path to the notes .md file."""

    if not os.path.exists(mp3_path):
        raise FileNotFoundError(f"File not found: {mp3_path}")

    log.info(f"Processing meeting: {mp3_path}")

    stem            = Path(mp3_path).stem
    transcript_file = os.path.join(transcript_dir, f"{stem}_transcript.txt")
    notes_file      = os.path.join(notes_dir,      f"{stem}_notes.md")

    language    = context.language if context else ""
    mic_speaker = getattr(context, "mic_speaker", "") if context else ""
    raw_text = transcribe_with_speakers(mp3_path, language=language, mic_speaker=mic_speaker)

    with open(transcript_file, "w", encoding="utf-8") as f:
        f.write(raw_text)
    log.info(f"Transcript saved: {transcript_file}")

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

    return notes_file
