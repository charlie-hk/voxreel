import contextlib
import io
import unittest

from voxreel.cli import main
from .helpers import HAVE_FFMPEG, ProjectCase


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(ProjectCase):
    def test_init_status_and_voice_commands(self):
        target = str(self.dir / "new")
        self.assertEqual(call("init", target)[0], 0)
        self.assertEqual(call("init", target)[0], 1)  # refuses to overwrite
        code, out, _ = call("status", target)
        self.assertEqual(code, 0)
        self.assertIn("intro", out)
        self.assertIn("narrator", call("voice", "list", target)[1])

    def test_clone_flow_and_refusals(self):
        folder = str(self.dir)
        sample = str(self.make_sample())
        base = ["voice", "clone", folder, "me", "--sample", sample, "--speaker", "Jane Doe",
                "--statement", "I, Jane Doe, agree to my voice being cloned for narration.",
                "--scope", "narration"]
        code, _, err = call(*base)  # no --confirm
        self.assertEqual(code, 1)
        self.assertIn("consent", err)
        self.assertEqual(call(*base, "--confirm")[0], 0)
        self.assertIn("speaker=Jane Doe", call("voice", "list", folder)[1])
        self.assertEqual(call("voice", "revoke", folder, "me", "--reason", "asked")[0], 0)
        self.assertIn("REVOKED", call("voice", "list", folder)[1])

    def test_errors_are_clean(self):
        code, _, err = call("run", str(self.dir / "missing.json"))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error:"))

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_run_via_cli(self):
        target = str(self.dir / "p")
        call("init", target)
        spec = (self.dir / "p" / "project.json")
        import json
        data = json.loads(spec.read_text())
        data["resolution"], data["fps"] = "320x180", 12
        spec.write_text(json.dumps(data))
        code, out, _ = call("run", target)
        self.assertEqual(code, 0)
        self.assertIn("done:", out)


if __name__ == "__main__":
    unittest.main()
