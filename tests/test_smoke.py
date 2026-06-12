import unittest
from types import SimpleNamespace

try:
    import numpy as np
    from audio_mix import mix_mono_tracks
except ModuleNotFoundError:
    np = None
    mix_mono_tracks = None

from filenames import obsidian_filename
from notes import build_notes
from settings_store import apply_settings, settings_payload


class FilenameTests(unittest.TestCase):
    def test_obsidian_filename_uses_safe_title(self):
        context = SimpleNamespace(title='Q2 / Planning: "North"', meeting_type="general")

        self.assertEqual(
            obsidian_filename("meeting_2026-05-09_13-45-00", context),
            "2026-05-09 Q2  Planning North.md",
        )

    def test_obsidian_filename_falls_back_to_type_and_time(self):
        context = SimpleNamespace(title="", meeting_type="standup")

        self.assertEqual(
            obsidian_filename("meeting_2026-05-09_13-45-00", context),
            "2026-05-09 Standup 13:45.md",
        )


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip(self):
        recorder = SimpleNamespace(
            mic_name="Mic",
            speaker_name="Speaker",
            mic_boost=1.5,
            sys_boost=0.8,
        )
        payload = settings_payload(True, False, recorder, "C:/Vault")

        target = SimpleNamespace()
        auto, realtime, vault = apply_settings(payload, target)

        self.assertTrue(auto)
        self.assertFalse(realtime)
        self.assertEqual(vault, "C:/Vault")
        self.assertEqual(target.mic_name, "Mic")
        self.assertEqual(target.speaker_name, "Speaker")
        self.assertEqual(target.mic_boost, 1.5)
        self.assertEqual(target.sys_boost, 0.8)


class NotesTests(unittest.TestCase):
    def test_append_notes_keeps_existing_notes(self):
        context = SimpleNamespace(title="Weekly")

        notes = build_notes(
            "transcript",
            context=context,
            notes_mode="append",
            existing_notes="old notes",
            notes_generator=lambda text, context=None, existing_notes="": "new notes",
        )

        self.assertEqual(notes, "old notes\n\n---\n\n# Weekly\n\nnew notes")

    def test_improve_notes_passes_existing_notes(self):
        context = SimpleNamespace(title="Weekly")
        calls = []

        def generator(text, context=None, existing_notes=""):
            calls.append(existing_notes)
            return "# Weekly\n\nbetter notes"

        notes = build_notes(
            "transcript",
            context=context,
            notes_mode="improve",
            existing_notes="old notes",
            notes_generator=generator,
        )

        self.assertEqual(calls, ["old notes"])
        self.assertEqual(notes, "# Weekly\n\nbetter notes")


class AudioMixTests(unittest.TestCase):
    @unittest.skipIf(np is None, "numpy is not installed")
    def test_mixing_preserves_longer_system_tail(self):
        mic = np.array([1.0, 1.0], dtype=np.float32)
        system = np.array([0.0, 0.0, 0.5, 0.5], dtype=np.float32)

        mixed = mix_mono_tracks(mic, system, mic_boost=1.0, sys_boost=1.0)

        self.assertEqual(len(mixed), 4)
        self.assertGreater(mixed[2], 0)
        self.assertGreater(mixed[3], 0)

    @unittest.skipIf(np is None, "numpy is not installed")
    def test_mixing_returns_none_without_audio(self):
        self.assertIsNone(mix_mono_tracks(None, None, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
