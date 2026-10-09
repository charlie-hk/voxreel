import json
import unittest
from datetime import date, timedelta

from voxreel.errors import ConsentError, SpecError
from .helpers import ProjectCase


class ConsentTests(ProjectCase):
    def test_stock_voice_needs_no_consent(self):
        voice, record = self.registry.resolve("narrator", "narration")
        self.assertEqual(voice.kind, "stock")
        self.assertIsNone(record)

    def test_clone_requires_confirmation_and_valid_statement(self):
        sample = self.make_sample()
        with self.assertRaises(ConsentError):
            self.registry.add_cloned("me", sample, "Jane Doe", "I, Jane Doe, agree to narration use.", ["narration"])
        with self.assertRaises(ConsentError):  # statement does not name the speaker
            self.registry.add_cloned("me", sample, "Jane Doe", "I agree to everything you want to do.",
                                     ["narration"], confirm=True)
        with self.assertRaises(ConsentError):  # too short
            self.registry.add_cloned("me", sample, "Jane Doe", "Jane Doe ok", ["narration"], confirm=True)
        with self.assertRaises(ConsentError):  # no scope
            self.registry.add_cloned("me", sample, "Jane Doe", "I, Jane Doe, agree to narration use.", [], confirm=True)
        self.assertNotIn("me", self.registry.voices())

    def test_cloned_voice_resolves_with_record(self):
        rec = self.clone()
        voice, record = self.registry.resolve("me", "narration")
        self.assertEqual(voice.kind, "cloned")
        self.assertEqual(record["id"], rec["id"])
        self.assertEqual(voice.sample_sha256, rec["sample_sha256"])

    def test_scope_is_enforced(self):
        self.clone(scope=["education"])
        with self.assertRaises(ConsentError):
            self.registry.resolve("me", "narration")

    def test_expiry(self):
        self.clone(expires=(date.today() + timedelta(days=5)).isoformat())
        self.registry.resolve("me", "narration")
        with self.assertRaises(ConsentError):
            self.registry.resolve("me", "narration", today=date.today() + timedelta(days=6))

    def test_revoke_blocks_use(self):
        self.clone()
        self.registry.revoke("me", "speaker asked")
        with self.assertRaises(ConsentError) as ctx:
            self.registry.resolve("me", "narration")
        self.assertIn("revoked", str(ctx.exception))
        with self.assertRaises(ConsentError):
            self.registry.revoke("me")  # already revoked

    def test_changed_sample_blocks_use(self):
        self.clone()
        (self.dir / "samples" / "me.wav").write_bytes(b"someone else's voice")
        with self.assertRaises(ConsentError) as ctx:
            self.registry.resolve("me", "narration")
        self.assertIn("changed", str(ctx.exception))

    def test_missing_consent_record_blocks_use(self):
        self.clone()
        (self.dir / "consent.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(ConsentError):
            self.registry.resolve("me", "narration")

    def test_unknown_voice_and_bad_ids(self):
        with self.assertRaises(SpecError):
            self.registry.resolve("ghost", "narration")
        with self.assertRaises(SpecError):
            self.registry.add_stock("Bad Id")

    def test_recording_is_hashed(self):
        rec = self.registry.add_cloned("me", self.make_sample(), "Jane Doe",
                                       "I, Jane Doe, agree to my voice being cloned for narration.",
                                       ["narration"], recording=self.make_sample("consent.wav"), confirm=True)
        self.assertEqual(len(rec["recording_sha256"]), 64)
        stored = json.loads((self.dir / "consent.json").read_text())
        self.assertEqual(stored[0]["speaker"], "Jane Doe")


if __name__ == "__main__":
    unittest.main()
