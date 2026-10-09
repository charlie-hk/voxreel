import json
import unittest
from unittest import mock

from voxreel import media, pipeline
from voxreel.errors import ConsentError, VoxError
from voxreel.providers import mock as mock_providers
from .helpers import HAVE_FFMPEG, ProjectCase


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class PipelineTests(ProjectCase):
    def test_end_to_end_and_cache(self):
        path = self.write_spec()
        first = pipeline.run(path, log=lambda *_: None)
        self.assertEqual((first["done"], first["cached"]), (9, 0))
        out = first["output"]
        self.assertTrue(out.is_file())
        self.assertGreater(media.probe_duration(out), 5)
        self.assertTrue(media.has_stream(out, "video") and media.has_stream(out, "audio"))

        prov = json.loads(first["provenance"].read_text())
        self.assertTrue(prov["ai_generated"])
        self.assertEqual(len(prov["scenes"]), 3)
        self.assertEqual(len(prov["output_sha256"]), 64)

        second = pipeline.run(path, log=lambda *_: None)
        self.assertEqual((second["done"], second["cached"]), (0, 9))

    def test_changing_one_scene_reruns_only_that_scene(self):
        path = self.write_spec()
        pipeline.run(path, log=lambda *_: None)
        spec = json.loads(path.read_text())
        spec["scenes"][1]["text"] = "A different sentence for the middle scene only."
        path.write_text(json.dumps(spec))
        result = pipeline.run(path, log=lambda *_: None)
        self.assertEqual(result["done"], 3)  # speech, video, mux of one scene
        self.assertEqual(result["cached"], 6)

    def test_metadata_marks_output_as_ai_generated(self):
        result = pipeline.run(self.write_spec(), log=lambda *_: None)
        tags = media.run([media.need("ffprobe"), "-v", "error", "-show_entries", "format_tags",
                          "-of", "json", str(result["output"])])
        text = json.dumps(json.loads(tags)).lower()
        self.assertIn("ai-generated", text)
        self.assertIn("provenance.json", text)

    def test_sidecar_subtitles(self):
        result = pipeline.run(self.write_spec(subtitles="sidecar"), log=lambda *_: None)
        self.assertTrue(result["output"].with_suffix(".srt").is_file())

    def test_unconsented_clone_blocks_before_any_provider_call(self):
        self.clone()
        self.registry.revoke("me")
        spec = json.loads(self.write_spec().read_text())
        spec["scenes"][2]["voice"] = "me"
        (self.dir / "project.json").write_text(json.dumps(spec))
        with mock.patch.object(mock_providers.MockTTS, "synthesize") as synth:
            with self.assertRaises(ConsentError):
                pipeline.run(self.dir / "project.json", log=lambda *_: None)
            synth.assert_not_called()

    def test_cloned_voice_with_consent_is_recorded_in_provenance(self):
        rec = self.clone()
        spec = json.loads(self.write_spec().read_text())
        spec["voice"] = "me"
        (self.dir / "project.json").write_text(json.dumps(spec))
        result = pipeline.run(self.dir / "project.json", log=lambda *_: None)
        prov = json.loads(result["provenance"].read_text())
        self.assertEqual({s["consent_id"] for s in prov["scenes"]}, {rec["id"]})

    def test_revoking_after_render_blocks_the_next_run(self):
        self.clone()
        spec = json.loads(self.write_spec().read_text())
        spec["voice"] = "me"
        path = self.dir / "project.json"
        path.write_text(json.dumps(spec))
        pipeline.run(path, log=lambda *_: None)
        self.registry.revoke("me")
        with self.assertRaises(ConsentError):
            pipeline.run(path, log=lambda *_: None)

    def test_only_unknown_scene(self):
        with self.assertRaises(VoxError):
            pipeline.run(self.write_spec(), only="nope", log=lambda *_: None)

    def test_only_refreshes_one_scene(self):
        path = self.write_spec()
        pipeline.run(path, log=lambda *_: None)
        result = pipeline.run(path, only="intro", force=True, log=lambda *_: None)
        self.assertTrue(result["output"].is_file())
        self.assertEqual(result["done"], 3)  # only the chosen scene is redone
        self.assertEqual(result["cached"], 6)


if __name__ == "__main__":
    unittest.main()
