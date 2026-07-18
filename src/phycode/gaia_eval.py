from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import string
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from phycode.audio import transcribe_audio
from phycode.composition import build_agent
from phycode.llm import LLMClient, OpenAICompatibleChatAdapter
from phycode.models import AgentProfile, SessionMode

IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("JSON metadata must be an array of objects")
        return payload
    try:
        import pyarrow.parquet as parquet  # type: ignore[reportMissingImports]
    except ImportError as exc:
        raise RuntimeError("Parquet evaluation requires `uv sync --extra gaia`") from exc
    return parquet.read_table(path).to_pylist()


def _read_credentials(path: Path, endpoint_index: int) -> tuple[str, str, str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    start = endpoint_index * 4
    if start + 2 >= len(lines):
        raise ValueError(f"Credential file does not contain endpoint index {endpoint_index}")
    base_url, api_key, model = lines[start : start + 3]
    if not base_url or not api_key or not model:
        raise ValueError(f"Credential block {endpoint_index} is incomplete")
    return base_url, api_key, model


def _normalize_number(value: str) -> float:
    for character in ("$", "%", ","):
        value = value.replace(character, "")
    try:
        return float(value)
    except ValueError:
        return float("inf")


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _normalize_text(value: str, *, remove_punctuation: bool = True) -> str:
    normalized = re.sub(r"\s", "", value).lower()
    if remove_punctuation:
        normalized = normalized.translate(str.maketrans("", "", string.punctuation))
    return normalized


def score_answer(model_answer: str | None, ground_truth: str) -> bool:
    """Match the current GAIA leaderboard scorer's type-dependent behavior."""
    answer = "None" if model_answer is None else model_answer
    if _is_float(ground_truth):
        return _normalize_number(answer) == float(ground_truth)
    if "," in ground_truth or ";" in ground_truth:
        expected_items = re.split(r"[,;]", ground_truth)
        answer_items = re.split(r"[,;]", answer)
        if len(expected_items) != len(answer_items):
            return False
        return all(
            _normalize_number(actual) == float(expected)
            if _is_float(expected)
            else _normalize_text(actual, remove_punctuation=False)
            == _normalize_text(expected, remove_punctuation=False)
            for actual, expected in zip(answer_items, expected_items)
        )
    return _normalize_text(answer) == _normalize_text(ground_truth)


def extract_answer(final_text: str | None) -> str:
    text = (final_text or "").strip()
    matches = re.findall(r"final\s+answer\s*:\s*(.+)", text, flags=re.IGNORECASE)
    return matches[-1].strip() if matches else text


def _load_existing_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    results: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if "summary" not in item and "task_id" in item:
            results.append(item)
    return results


def _rewrite_results(path: Path, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(result, ensure_ascii=True) + "\n" for result in results)
    path.write_text(content, encoding="utf-8")


def _append_result(path: Path, result: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=True) + "\n")


def _attachment_source(dataset_root: Path, row: dict[str, Any]) -> Path | None:
    file_path = row.get("file_path") or row.get("file_name")
    if not file_path:
        return None
    candidate = (dataset_root / str(file_path)).resolve()
    root = dataset_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Attachment path escapes dataset root: {file_path}")
    if not candidate.is_file():
        raise FileNotFoundError(f"Declared attachment does not exist: {file_path}")
    return candidate


def _run_case(
    row: dict[str, Any],
    dataset_root: Path,
    llm: LLMClient,
    max_tool_calls: int,
    run_root: Path,
    vision_model: str | None = None,
    audio_model: str | None = None,
    vision_inspector: Callable[[Path, str], str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    task_id = str(row.get("task_id", "unknown"))
    question = str(row.get("Question", row.get("question", "")))
    expected = str(row.get("Final answer", row.get("answer", "")))
    workspace = Path(tempfile.mkdtemp(prefix=f"{task_id[:12]}-", dir=run_root))
    attachment = _attachment_source(dataset_root, row)
    configured_vision = vision_inspector
    if configured_vision is None and vision_model:
        configured_vision = getattr(llm, "inspect_image", None)
    if attachment is not None:
        if attachment.suffix.lower() in IMAGE_SUFFIXES and configured_vision is None:
            raise RuntimeError("Image attachment requires --vision-model")
        if attachment.suffix.lower() in AUDIO_SUFFIXES and not audio_model:
            raise RuntimeError("Audio attachment requires --audio-model")
        target = workspace / attachment.name
        shutil.copy2(attachment, target)
        if attachment.suffix.lower() in AUDIO_SUFFIXES:
            transcript = transcribe_audio(target, audio_model or "")
            question += f"\n\nThe local transcript of the attached audio follows:\n{transcript}"
        elif attachment.suffix.lower() in IMAGE_SUFFIXES:
            visual_prompt = (
                "Perform only the precise visual analysis needed to answer the exact question below. Preserve source "
                "order and exact visible text. Distinguish source expressions from answers the question asks you to "
                "derive, and do not include unrequested intermediate items. If a question asks for lesson items plus "
                "answers to exercises, do not also list the exercise inputs as lesson items unless it explicitly asks "
                "for them. Return concise evidence and a proposed answer, not a general tutorial.\n\n"
                f"Question: {question}"
            )
            visual_evidence = configured_vision(target, visual_prompt) if configured_vision is not None else ""
            question += (
                "\n\nA local vision model analyzed the attached image. Use its evidence to answer the exact question; "
                "exclude source or intermediate items that were not requested:\n"
                f"{visual_evidence}"
            )
        else:
            question += f"\n\nAn attachment is available at {target.name}. Use file.inspect to inspect it when appropriate."

    previous_cwd = Path.cwd()
    try:
        os.chdir(workspace)
        result = build_agent(
            SessionMode.NON_INTERACTIVE,
            llm=llm,
            profile=AgentProfile.GAIA,
            max_tool_calls=max_tool_calls,
        ).run(question)
    finally:
        os.chdir(previous_cwd)

    answer = extract_answer(result.final_text)
    return {
        "task_id": task_id,
        "level": row.get("Level"),
        "expected": expected,
        "answer": answer,
        "correct": bool(expected) and score_answer(answer, expected),
        "stopped_reason": result.stopped_reason,
        "event_count": len(result.events),
        "elapsed_s": round(time.monotonic() - started, 2),
        "attachment": attachment.name if attachment is not None else None,
    }


def evaluate(
    metadata_path: Path,
    dataset_root: Path,
    credentials_path: Path,
    endpoint_index: int = 0,
    level: int | None = None,
    task_ids: list[str] | None = None,
    limit: int | None = None,
    max_tool_calls: int = 12,
    vision_model: str | None = None,
    vision_endpoint_index: int | None = None,
    audio_model: str | None = "base.en",
    output_path: Path | None = None,
    resume: bool = False,
    llm_timeout_seconds: float = 120.0,
    llm_max_retries: int = 0,
) -> dict[str, Any]:
    base_url, api_key, model = _read_credentials(credentials_path, endpoint_index)
    rows = _load_rows(metadata_path)
    if level is not None:
        rows = [row for row in rows if int(row.get("Level", 0)) == level]
    if task_ids:
        requested_ids = set(task_ids)
        rows = [row for row in rows if str(row.get("task_id", "unknown")) in requested_ids]
        found_ids = {str(row.get("task_id", "unknown")) for row in rows}
        missing_ids = requested_ids - found_ids
        if missing_ids:
            raise ValueError(f"Unknown task ID(s): {', '.join(sorted(missing_ids))}")
    if limit is not None:
        rows = rows[:limit]

    selected_ids = {str(row.get("task_id", "unknown")) for row in rows}
    existing_results = _load_existing_results(output_path) if output_path is not None and resume else []
    results = [result for result in existing_results if str(result.get("task_id")) in selected_ids]
    completed_ids = {str(result.get("task_id")) for result in results}
    if output_path is not None:
        _rewrite_results(output_path, results)

    runtime_root = Path.cwd() / ".phycode"
    runtime_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="phycode-gaia-", dir=runtime_root))
    llm = OpenAICompatibleChatAdapter(
        base_url,
        model,
        api_key,
        vision_model=vision_model if vision_endpoint_index is None else None,
        timeout_seconds=llm_timeout_seconds,
        max_retries=llm_max_retries,
    )
    vision_inspector: Callable[[Path, str], str] | None = None
    if vision_model:
        if vision_endpoint_index is None:
            vision_inspector = llm.inspect_image
        else:
            vision_base_url, vision_api_key, vision_text_model = _read_credentials(
                credentials_path, vision_endpoint_index
            )
            vision_adapter = OpenAICompatibleChatAdapter(
                vision_base_url,
                vision_text_model,
                vision_api_key,
                vision_model=vision_model,
                timeout_seconds=llm_timeout_seconds,
                max_retries=llm_max_retries,
            )
            vision_inspector = vision_adapter.inspect_image
    for row in rows:
        if str(row.get("task_id", "unknown")) in completed_ids:
            continue
        try:
            result = _run_case(
                row,
                dataset_root,
                llm,
                max_tool_calls,
                run_root,
                vision_model,
                audio_model,
                vision_inspector,
            )
        except Exception as exc:
            result = {
                "task_id": row.get("task_id", "unknown"),
                "level": row.get("Level"),
                "correct": False,
                "stopped_reason": "harness_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        results.append(result)
        completed_ids.add(str(result.get("task_id")))
        if output_path is not None:
            _append_result(output_path, result)
        print(json.dumps(result, ensure_ascii=True), flush=True)

    level_values = sorted({str(result.get("level")) for result in results if result.get("level") is not None})
    summary = {
        "count": len(results),
        "correct": sum(1 for result in results if result.get("correct")),
        "accuracy": (sum(1 for result in results if result.get("correct")) / len(results)) if results else 0.0,
        "by_level": {
            level_value: {
                "count": sum(1 for result in results if str(result.get("level")) == level_value),
                "correct": sum(1 for result in results if str(result.get("level")) == level_value and result.get("correct")),
            }
            for level_value in level_values
        },
    }
    if output_path is not None:
        _append_result(output_path, {"summary": summary})
    print(json.dumps({"summary": summary}, ensure_ascii=True), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PhyCode against a local GAIA metadata split")
    parser.add_argument("--metadata", dest="metadata_path", type=Path, required=True, help="metadata.parquet or JSONL")
    parser.add_argument("--dataset-root", type=Path, required=True, help="GAIA repository root containing attachments")
    parser.add_argument(
        "--credentials", dest="credentials_path", type=Path, required=True, help="Local endpoint/key/model file"
    )
    parser.add_argument("--endpoint-index", type=int, default=0)
    parser.add_argument("--level", type=int)
    parser.add_argument("--task-id", dest="task_ids", action="append", help="Run one task ID; may be repeated")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--vision-model", help="Optional vision-capable model for image attachments")
    parser.add_argument("--vision-endpoint-index", type=int, help="Credential block used only for image inspection")
    parser.add_argument("--audio-model", default="base.en", help="Local faster-whisper model or model path")
    parser.add_argument("--output", dest="output_path", type=Path)
    parser.add_argument("--resume", action="store_true", help="Reuse completed task IDs from an existing output JSONL")
    parser.add_argument("--llm-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--llm-max-retries", type=int, default=0)
    args = parser.parse_args()
    evaluate(**vars(args))


if __name__ == "__main__":
    main()
