"""Speak text in a cloned voice with Coqui XTTS-v2. Meant to be run by voxreel's `command` provider.

Setup (a separate Python 3.10-3.12 environment is recommended; PyTorch does not follow the newest Python quickly):

    python -m venv xtts-env
    xtts-env\\Scripts\\activate          (Windows)   or   source xtts-env/bin/activate
    pip install coqui-tts

Licence: the XTTS-v2 model weights use the Coqui Public Model License, which is non-commercial.
Read it before using the output for client work: https://coqui.ai/cpml
Pass --agree-license once you have read it (the model download otherwise asks interactively).

Other languages (for example Persian): XTTS-v2 itself does not speak them, but community fine-tunes do.
Point the tool at one with --hf-repo (downloaded through huggingface_hub; gated repositories need
`hf auth login` first) or --model-dir (a folder with config.json and the checkpoint files), and pass
--language with that model's language code. The tool cannot judge such a model; check its licence and card.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"
LANGUAGES = "en es fr de it pt pl tr ru nl cs ar zh-cn ja hu ko hi"


def fail(message: str) -> int:
    print(f"xtts: {message}", file=sys.stderr)
    return 2


def to_reference_wav(sample: Path, workdir: Path) -> Path:
    """XTTS reads wav most reliably; convert whatever the user recorded (m4a, mp3, ...)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to prepare the voice sample")
    out = workdir / "reference.wav"
    proc = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(sample), "-ac", "1", "-ar", "24000",
                           str(out)], capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"could not read the voice sample: {proc.stderr.strip()[-300:]}")
    return out


def resolve_model_dir(hf_repo: str, model_dir: str) -> Path:
    if model_dir:
        folder = Path(model_dir)
    else:
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            raise RuntimeError("huggingface_hub is not installed (pip install huggingface_hub)") from None
        try:
            folder = Path(snapshot_download(repo_id=hf_repo))
        except Exception as exc:  # noqa: BLE001 - the hub raises many unrelated types
            raise RuntimeError(f"could not download {hf_repo!r}: {exc}. If the repository is gated, accept its "
                               "terms on the website and run `hf auth login` first.") from exc
    if not (folder / "config.json").is_file():
        raise RuntimeError(f"{folder} has no config.json; it is not an XTTS-style model folder")
    return folder


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m voxreel.tools.xtts", description=__doc__.split("\n")[0])
    p.add_argument("--text-file", required=True)
    p.add_argument("--speaker-wav", default="", help="the cloned voice's sample (voxreel passes {sample})")
    p.add_argument("--language", default="en", help=f"one of: {LANGUAGES}")
    p.add_argument("--out", required=True)
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    p.add_argument("--hf-repo", default="", help="Hugging Face repo id of an XTTS fine-tune (e.g. for Persian)")
    p.add_argument("--model-dir", default="", help="local folder of an XTTS-style model (config.json + checkpoint)")
    p.add_argument("--agree-license", action="store_true", help="I have read the Coqui Public Model License")
    args = p.parse_args(argv)

    if not args.speaker_wav or not Path(args.speaker_wav).is_file():
        return fail("this provider needs a cloned voice. Register one with `voxreel voice clone` and use it "
                    "in the project (stock voices have no sample).")
    text_path = Path(args.text_file)
    if not text_path.is_file():
        return fail(f"text file not found: {text_path}")
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        return fail("the text is empty")
    custom = bool(args.hf_repo or args.model_dir)
    if args.hf_repo and args.model_dir:
        return fail("use either --hf-repo or --model-dir, not both")
    if not custom and args.language not in LANGUAGES.split():
        return fail(f"XTTS-v2 does not support language {args.language!r}; supported: {LANGUAGES}. "
                    "For other languages use a community fine-tune with --hf-repo or --model-dir.")

    if args.agree_license:
        os.environ["COQUI_TOS_AGREED"] = "1"
    try:
        import torch
        from TTS.api import TTS
    except ImportError:
        return fail("the TTS library is not installed in this Python. Run: pip install coqui-tts "
                    "(see the instructions at the top of voxreel/tools/xtts.py)")

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            reference = to_reference_wav(Path(args.speaker_wav), Path(tmp))
        except RuntimeError as exc:
            return fail(str(exc))
        if custom:
            try:
                model_dir = resolve_model_dir(args.hf_repo, args.model_dir)
            except RuntimeError as exc:
                return fail(str(exc))
            tts = TTS(model_path=str(model_dir), config_path=str(model_dir / "config.json")).to(device)
        else:
            tts = TTS(MODEL).to(device)
        tts.tts_to_file(text=text, speaker_wav=str(reference), language=args.language, file_path=args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
