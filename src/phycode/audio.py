from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_AUDIO_BYTES = 50_000_000


@lru_cache(maxsize=2)
def _load_whisper_model(model_name: str) -> Any:
    try:
        from faster_whisper import WhisperModel  # type: ignore[reportMissingImports]
    except ImportError as exc:
        raise RuntimeError("Audio transcription requires `uv sync --extra gaia`") from exc
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def transcribe_audio(path: Path, model_name: str) -> str:
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError(f"Audio attachment exceeds {MAX_AUDIO_BYTES} bytes")
    model = _load_whisper_model(model_name)
    segments, info = model.transcribe(
        str(path),
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    lines = [
        f"Detected language: {info.language} (probability {info.language_probability:.3f})",
        f"Duration: {info.duration:.2f} seconds",
    ]
    lines.extend(f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text.strip()}" for segment in segments)
    return "\n".join(lines)
