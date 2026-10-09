import json
import unittest

from voxreel import srt
from voxreel.errors import SpecError
from voxreel.project import load_spec
from .helpers import ProjectCase


class SpecTests(ProjectCase):
    def test_valid(self):
        spec = load_spec(self.write_spec())
        self.assertEqual((spec.width, spec.height, spec.fps), (320, 180, 12))
        self.assertEqual(len(spec.scenes), 3)
        self.assertEqual(spec.scenes[0].voice, "narrator")

    def test_rejects_bad_input(self):
        bad = [
            {"resolution": "1281x720"}, {"resolution": "big"}, {"fps": 0}, {"scenes": []},
            {"output": "../escape.mp4"}, {"output": "out/video.avi"}, {"subtitles": "maybe"},
            {"scenes": [{"id": "A B", "text": "x"}]},
            {"scenes": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]},
            {"scenes": [{"id": "a", "text": ""}]},
            {"scenes": [{"id": "a", "text": "x" * 2001}]},
            {"voice": None, "scenes": [{"id": "a", "text": "hello"}]},
        ]
        for override in bad:
            with self.subTest(override=override):
                with self.assertRaises(SpecError):
                    load_spec(self.write_spec(**override))

    def test_invalid_json(self):
        (self.dir / "project.json").write_text("{nope", encoding="utf-8")
        with self.assertRaises(SpecError):
            load_spec(self.dir / "project.json")


class SrtTests(unittest.TestCase):
    def test_stamp(self):
        self.assertEqual(srt.stamp(3723.5), "01:02:03,500")

    def test_chunks_respect_limit(self):
        parts = srt.chunks("One two three. " + "word " * 40)
        self.assertTrue(all(len(p) <= srt.MAX_CHARS for p in parts))
        self.assertEqual(parts[0], "One two three.")

    def test_cues_cover_scene_time(self):
        out = srt.build([("Hello there.", 2.0), ("Second scene.", 3.0)])
        self.assertIn("00:00:00,000 --> 00:00:02,000", out)
        self.assertIn("00:00:02,000 --> 00:00:05,000", out)


if __name__ == "__main__":
    unittest.main()
