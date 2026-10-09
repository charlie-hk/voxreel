class VoxError(Exception):
    """Base class for all expected voxreel failures."""


class SpecError(VoxError):
    """The project file is invalid."""


class ConsentError(VoxError):
    """A voice cannot be used because consent is missing, revoked, expired or out of scope."""


class ProviderError(VoxError):
    """A TTS or video provider failed."""


class MediaError(VoxError):
    """ffmpeg/ffprobe failed or is missing."""
