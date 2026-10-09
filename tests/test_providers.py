import base64
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from voxreel.errors import ProviderError
from voxreel.providers import VideoRequest, VoiceRef, get_tts, get_video
from voxreel.providers import http as http_provider
from .helpers import HAVE_FFMPEG, ProjectCase

WAV_SCRIPT = (
    "import sys, wave; "
    "text = open(sys.argv[1], encoding='utf-8').read(); "
    "w = wave.open(sys.argv[2], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); "
    "w.writeframes(b'\\x00\\x10' * (800 * (1 + len(text) // 10))); w.close()"
)
VOICE = VoiceRef("narrator", "stock", "pv-1")


class CommandProviderTests(ProjectCase):
    def test_tts_command(self):
        out = self.dir / "a.wav"
        tts = get_tts("command", {"cmd": [sys.executable, "-c", WAV_SCRIPT, "{text_file}", "{out}"]})
        tts.synthesize("hello world, this is a test", VOICE, out)
        self.assertGreater(out.stat().st_size, 1000)

    def test_command_failure_and_missing_output(self):
        out = self.dir / "a.wav"
        with self.assertRaises(ProviderError):
            get_tts("command", {"cmd": [sys.executable, "-c", "raise SystemExit(3)"]}).synthesize("x", VOICE, out)
        with self.assertRaises(ProviderError):
            get_tts("command", {"cmd": [sys.executable, "-c", "pass"]}).synthesize("x", VOICE, out)
        with self.assertRaises(ProviderError):
            get_tts("command", {"cmd": ["definitely-not-installed-xyz"]}).synthesize("x", VOICE, out)
        with self.assertRaises(ProviderError):
            get_tts("command", {}).synthesize("x", VOICE, out)

    def test_text_is_never_interpreted_by_a_shell(self):
        marker = self.dir / "pwned"
        out = self.dir / "a.wav"
        tts = get_tts("command", {"cmd": [sys.executable, "-c", WAV_SCRIPT, "{text_file}", "{out}"]})
        tts.synthesize(f"hi; touch {marker}; $(touch {marker})", VOICE, out)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_video_command(self):
        out = self.dir / "c.mp4"
        cmd = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s={width}x{height}:d={duration}",
               "-pix_fmt", "yuv420p", "{out}"]
        get_video("command", {"cmd": cmd}).generate(VideoRequest("p", 1.0, 160, 90, 12), out)
        self.assertGreater(out.stat().st_size, 500)


class Handler(BaseHTTPRequestHandler):
    jobs = 0
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append((self.path, self.headers.get("Authorization"), payload))
        if self.path == "/binary":
            self._send(b"RIFFfake-audio", "audio/wav")
        elif self.path == "/b64":
            self._send(json.dumps({"audio": base64.b64encode(b"abc123").decode()}).encode())
        elif self.path == "/submit":
            self._send(json.dumps({"id": "job-7"}).encode())
        elif self.path == "/flaky":
            Handler.jobs += 1
            if Handler.jobs < 3:
                self._send(b'{"error":"busy"}', code=503)
            else:
                self._send(b"ok-after-retry", "application/octet-stream")
        else:
            self._send(b"{}", code=404)

    def do_GET(self):
        if self.path == "/jobs/job-7":
            Handler.jobs += 1
            status = "succeeded" if Handler.jobs >= 2 else "running"
            port = self.server.server_address[1]
            self._send(json.dumps({"status": status, "output": [f"http://127.0.0.1:{port}/file.mp4"]}).encode())
        elif self.path == "/file.mp4":
            self._send(b"VIDEO-BYTES", "video/mp4")
        else:
            self._send(b"{}", code=404)


class HttpProviderTests(ProjectCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        super().setUp()
        Handler.jobs = 0
        Handler.seen = []
        self.out = self.dir / "o.bin"

    def test_binary_with_env_secret(self):
        cfg = {"url": self.base + "/binary", "headers": {"Authorization": "Bearer ${ENV:VX_KEY}"},
               "body": {"text": "{text}", "voice": "{voice}"}}
        with mock.patch.dict(os.environ, {"VX_KEY": "s3cret"}):
            get_tts("http", cfg).synthesize("hello", VOICE, self.out)
        self.assertEqual(self.out.read_bytes(), b"RIFFfake-audio")
        path, auth, payload = Handler.seen[0]
        self.assertEqual(auth, "Bearer s3cret")
        self.assertEqual(payload, {"text": "hello", "voice": "pv-1"})

    def test_missing_env_var(self):
        cfg = {"url": self.base + "/binary", "headers": {"Authorization": "Bearer ${ENV:VX_NOPE_NOT_SET}"}}
        with self.assertRaises(ProviderError) as ctx:
            get_tts("http", cfg).synthesize("x", VOICE, self.out)
        self.assertIn("VX_NOPE_NOT_SET", str(ctx.exception))

    def test_user_text_cannot_inject_env_or_placeholders(self):
        cfg = {"url": self.base + "/binary", "body": {"text": "{text}", "voice": "{voice}"}}
        with mock.patch.dict(os.environ, {"VX_KEY": "s3cret"}):
            get_tts("http", cfg).synthesize("leak ${ENV:VX_KEY} and {voice}", VOICE, self.out)
        self.assertEqual(Handler.seen[0][2]["text"], "leak ${ENV:VX_KEY} and {voice}")

    def test_json_base64(self):
        cfg = {"url": self.base + "/b64", "body": {}, "response": "json_b64", "result_path": "audio"}
        get_tts("http", cfg).synthesize("x", VOICE, self.out)
        self.assertEqual(self.out.read_bytes(), b"abc123")

    def test_async_job_with_polling(self):
        cfg = {"url": self.base + "/submit", "body": {"prompt": "{prompt}"},
               "poll": {"job_path": "id", "url": self.base + "/jobs/{job_id}", "status_path": "status",
                        "done": ["succeeded"], "failed": ["failed"], "result_path": "output.0",
                        "result": "json_url", "interval": 0.01, "timeout": 10}}
        get_video("http", cfg).generate(VideoRequest("a cat", 2, 320, 180, 12), self.out)
        self.assertEqual(self.out.read_bytes(), b"VIDEO-BYTES")

    def test_retries_on_503(self):
        get_tts("http", {"url": self.base + "/flaky", "body": {}}).synthesize("x", VOICE, self.out)
        self.assertEqual(self.out.read_bytes(), b"ok-after-retry")

    def test_http_error_is_reported(self):
        with self.assertRaises(ProviderError) as ctx:
            get_tts("http", {"url": self.base + "/nope", "body": {}}).synthesize("x", VOICE, self.out)
        self.assertIn("404", str(ctx.exception))

    def test_plain_http_to_remote_host_is_refused(self):
        with self.assertRaises(ProviderError):
            http_provider._check_url("http://example.com/api")
        http_provider._check_url("https://example.com/api")
        http_provider._check_url("http://localhost:9/api")

    def test_unknown_provider(self):
        with self.assertRaises(ProviderError):
            get_tts("nope", {})


if __name__ == "__main__":
    unittest.main()


class XttsToolTests(ProjectCase):
    """The model itself cannot run in CI; these cover everything around it."""

    def run_tool(self, voice, extra=()):
        out = self.dir / "o.wav"
        text = self.dir / "t.txt"
        text.write_text("hello there", encoding="utf-8")
        tts = get_tts("command", {"cmd": ["{python}", "-m", "voxreel.tools.xtts", "--text-file", "{text_file}",
                                          "--speaker-wav", "{sample}", "--out", "{out}", *extra]})
        tts.synthesize("hello there", voice, out)

    def test_stock_voice_gets_a_clear_message(self):
        with self.assertRaises(ProviderError) as ctx:
            self.run_tool(VOICE)
        self.assertIn("needs a cloned voice", str(ctx.exception))

    def test_missing_library_gets_a_clear_message(self):
        sample = self.make_sample()
        voice = VoiceRef("me", "cloned", None, sample, "x")
        with self.assertRaises(ProviderError) as ctx:
            self.run_tool(voice)
        self.assertIn("pip install coqui-tts", str(ctx.exception))

    def test_unsupported_language(self):
        sample = self.make_sample()
        voice = VoiceRef("me", "cloned", None, sample, "x")
        with self.assertRaises(ProviderError) as ctx:
            self.run_tool(voice, ["--language", "fa"])
        self.assertIn("does not support language", str(ctx.exception))

    def test_custom_model_allows_other_languages_and_checks_the_folder(self):
        sample = self.make_sample()
        voice = VoiceRef("me", "cloned", None, sample, "x")
        with self.assertRaises(ProviderError) as ctx:
            self.run_tool(voice, ["--language", "fa", "--model-dir", str(self.dir / "nope")])
        message = str(ctx.exception)
        self.assertNotIn("does not support language", message)
        # either the TTS library is missing in CI, or the folder is rejected: both are clear messages
        self.assertTrue("pip install coqui-tts" in message or "no config.json" in message)

    def test_cannot_combine_model_sources(self):
        sample = self.make_sample()
        voice = VoiceRef("me", "cloned", None, sample, "x")
        with self.assertRaises(ProviderError) as ctx:
            self.run_tool(voice, ["--hf-repo", "a/b", "--model-dir", str(self.dir)])
        self.assertIn("not both", str(ctx.exception))
