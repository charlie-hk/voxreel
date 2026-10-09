import json
import shutil
import tempfile
import unittest
from pathlib import Path

from voxreel.cli import EXAMPLE
from voxreel.consent import Registry

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.registry = Registry(self.dir)
        self.registry.add_stock("narrator")

    def write_spec(self, **overrides):
        spec = json.loads(json.dumps(EXAMPLE))
        spec["resolution"] = "320x180"
        spec["fps"] = 12
        spec.update(overrides)
        path = self.dir / "project.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def make_sample(self, name="me.wav", seconds=1.0):
        import math, struct, wave
        path = self.dir / name
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 9))) for i in range(int(16000 * seconds))))
        return path

    def clone(self, voice_id="me", scope=("narration",), expires=None):
        return self.registry.add_cloned(
            voice_id, self.make_sample(), "Jane Doe", "I, Jane Doe, agree to my voice being cloned for narration.",
            list(scope), expires=expires, confirm=True)
