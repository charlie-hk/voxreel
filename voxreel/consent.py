"""Voice registry and consent records.

Cloned voices cannot be used without a consent record that is tied to the exact sample file
(by SHA-256), has not been revoked or expired, and covers the project's purpose.

This is an audit trail and a guard rail, not identity verification: the operator attests that the
speaker agreed. voxreel cannot check who is speaking in a sample.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from .errors import ConsentError, SpecError
from .providers.base import VoiceRef

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SpecError(f"{path.name} is not valid JSON: {exc}") from exc


class Registry:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir)
        self.voices_path = self.dir / "voices.json"
        self.consent_path = self.dir / "consent.json"

    # --- reading -----------------------------------------------------------

    def voices(self) -> dict:
        return _read_json(self.voices_path, {})

    def consents(self) -> list:
        return _read_json(self.consent_path, [])

    # --- adding ------------------------------------------------------------

    def add_stock(self, voice_id: str, provider_voice: str | None = None) -> None:
        self._check_id(voice_id)
        voices = self.voices()
        if voice_id in voices:
            raise SpecError(f"voice {voice_id!r} already exists")
        voices[voice_id] = {"kind": "stock", "provider_voice": provider_voice}
        _write_json(self.voices_path, voices)

    def add_cloned(self, voice_id: str, sample: Path, speaker: str, statement: str, scope: list[str],
                   expires: str | None = None, recording: Path | None = None, confirm: bool = False,
                   provider_voice: str | None = None) -> dict:
        self._check_id(voice_id)
        if not confirm:
            raise ConsentError("cloning requires an explicit confirmation that the speaker consented "
                               "(pass --confirm after you have their permission)")
        speaker = speaker.strip()
        if not speaker:
            raise ConsentError("speaker name is required")
        if len(statement.strip()) < 20 or speaker.lower() not in statement.lower():
            raise ConsentError("the consent statement must name the speaker and say what they agree to "
                               "(at least 20 characters)")
        scope = [s.strip().lower() for s in scope if s.strip()]
        if not scope:
            raise ConsentError("scope is required, for example: narration")
        if expires:
            try:
                date.fromisoformat(expires)
            except ValueError as exc:
                raise ConsentError("expires must look like 2027-12-31") from exc
        sample = Path(sample)
        if not sample.is_file() or sample.stat().st_size == 0:
            raise ConsentError(f"sample file not found or empty: {sample}")
        voices = self.voices()
        if voice_id in voices:
            raise SpecError(f"voice {voice_id!r} already exists")
        samples_dir = self.dir / "samples"
        samples_dir.mkdir(exist_ok=True)
        stored = samples_dir / f"{voice_id}{sample.suffix.lower()}"
        shutil.copyfile(sample, stored)
        record = {
            "id": "c_" + hashlib.sha256(f"{voice_id}{speaker}{_now()}".encode()).hexdigest()[:10],
            "voice_id": voice_id,
            "speaker": speaker,
            "statement": statement.strip(),
            "scope": scope,
            "created_at": _now(),
            "expires_at": expires,
            "sample_sha256": sha256_file(stored),
            "recording_sha256": None,
            "revoked_at": None,
            "revoke_reason": None,
        }
        if recording:
            recording = Path(recording)
            if not recording.is_file():
                raise ConsentError(f"consent recording not found: {recording}")
            kept = samples_dir / f"{voice_id}.consent{recording.suffix.lower()}"
            shutil.copyfile(recording, kept)
            record["recording_sha256"] = sha256_file(kept)
        records = self.consents()
        records.append(record)
        _write_json(self.consent_path, records)
        voices[voice_id] = {"kind": "cloned", "provider_voice": provider_voice,
                            "sample": f"samples/{stored.name}", "consent_id": record["id"]}
        _write_json(self.voices_path, voices)
        return record

    # --- revoking ----------------------------------------------------------

    def revoke(self, voice_id: str, reason: str = "") -> None:
        voices = self.voices()
        if voice_id not in voices:
            raise SpecError(f"unknown voice {voice_id!r}")
        records = self.consents()
        hit = False
        for rec in records:
            if rec["voice_id"] == voice_id and not rec["revoked_at"]:
                rec["revoked_at"] = _now()
                rec["revoke_reason"] = reason or None
                hit = True
        if not hit and voices[voice_id]["kind"] == "cloned":
            raise ConsentError(f"voice {voice_id!r} is already revoked")
        _write_json(self.consent_path, records)

    # --- the guard ---------------------------------------------------------

    def resolve(self, voice_id: str, purpose: str, today: date | None = None) -> tuple[VoiceRef, dict | None]:
        voices = self.voices()
        if voice_id not in voices:
            raise SpecError(f"unknown voice {voice_id!r}; add it with `voxreel voice add-stock` or `voice clone`")
        v = voices[voice_id]
        if v["kind"] == "stock":
            return VoiceRef(voice_id, "stock", v.get("provider_voice")), None
        record = next((r for r in self.consents() if r["id"] == v.get("consent_id")), None)
        if record is None or record["voice_id"] != voice_id:
            raise ConsentError(f"voice {voice_id!r} has no consent record")
        if record["revoked_at"]:
            raise ConsentError(f"consent for voice {voice_id!r} was revoked on {record['revoked_at']}")
        if record["expires_at"] and (today or date.today()) > date.fromisoformat(record["expires_at"]):
            raise ConsentError(f"consent for voice {voice_id!r} expired on {record['expires_at']}")
        if purpose.lower() not in record["scope"]:
            raise ConsentError(f"consent for voice {voice_id!r} covers {record['scope']}, not {purpose!r}")
        sample = self.dir / v["sample"]
        if not sample.is_file():
            raise ConsentError(f"sample for voice {voice_id!r} is missing")
        digest = sha256_file(sample)
        if digest != record["sample_sha256"]:
            raise ConsentError(f"sample for voice {voice_id!r} changed since consent was recorded")
        return VoiceRef(voice_id, "cloned", v.get("provider_voice"), sample, digest), record

    @staticmethod
    def _check_id(voice_id: str) -> None:
        if not ID_RE.match(voice_id):
            raise SpecError("voice id must be lowercase letters, digits, - or _ (max 40 characters)")
