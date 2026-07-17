import sys
from pathlib import Path

import pytest

import phycode.gaia_eval as gaia_eval
from phycode.gaia_eval import (
    _attachment_source,
    _load_existing_results,
    _load_rows,
    _rewrite_results,
    _run_case,
    extract_answer,
    score_answer,
)
from phycode.models import AgentEvent, AgentEventType


def test_gaia_answer_extraction():
    assert extract_answer("Reasoning\nFINAL ANSWER: White; 5,876") == "White; 5,876"


@pytest.mark.parametrize(
    ("model_answer", "ground_truth", "expected"),
    [
        ("$1,234", "1234", True),
        ("Saint Petersburg.", "Saint Petersburg", True),
        ("Green; White", "green, white", True),
        ("White; $5876", "White; 5876", True),
        ("White; 5,876", "White; 5876", False),
        ("White; Green", "Green; White", False),
        ("A.B, C", "AB, C", False),
        (None, "None", True),
    ],
)
def test_gaia_answer_scoring_matches_official_type_rules(model_answer, ground_truth, expected):
    assert score_answer(model_answer, ground_truth) is expected


def test_gaia_jsonl_loader(tmp_path: Path):
    metadata = tmp_path / "metadata.jsonl"
    metadata.write_text('{"task_id":"1","Question":"q"}\n', encoding="utf-8")

    assert _load_rows(metadata) == [{"task_id": "1", "Question": "q"}]


def test_gaia_json_array_loader(tmp_path: Path):
    metadata = tmp_path / "metadata.json"
    metadata.write_text('[{"task_id":"1","Question":"q"}]', encoding="utf-8")

    assert _load_rows(metadata) == [{"task_id": "1", "Question": "q"}]


def test_gaia_resume_loader_ignores_previous_summary(tmp_path: Path):
    output = tmp_path / "results.jsonl"
    _rewrite_results(output, [{"task_id": "1", "correct": True}])
    with output.open("a", encoding="utf-8") as handle:
        handle.write('{"summary":{"count":1}}\n')

    assert _load_existing_results(output) == [{"task_id": "1", "correct": True}]


def test_gaia_cli_maps_paths_to_evaluator_parameters(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_evaluate(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(gaia_eval, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gaia_eval",
            "--metadata",
            str(tmp_path / "metadata.parquet"),
            "--dataset-root",
            str(tmp_path),
            "--credentials",
            str(tmp_path / "credentials.txt"),
            "--output",
            str(tmp_path / "results.jsonl"),
            "--task-id",
            "task-1",
        ],
    )

    gaia_eval.main()

    assert captured["metadata_path"] == tmp_path / "metadata.parquet"
    assert captured["credentials_path"] == tmp_path / "credentials.txt"
    assert captured["output_path"] == tmp_path / "results.jsonl"
    assert captured["task_ids"] == ["task-1"]


def test_gaia_attachment_path_stays_inside_dataset_root(tmp_path: Path):
    attachment = tmp_path / "2023" / "validation" / "file.txt"
    attachment.parent.mkdir(parents=True)
    attachment.write_text("evidence", encoding="utf-8")
    row = {"file_path": "2023/validation/file.txt"}

    assert _attachment_source(tmp_path, row) == attachment.resolve()


def test_gaia_attachment_path_escape_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        _attachment_source(tmp_path, {"file_path": "../outside.txt"})


def test_gaia_declared_missing_attachment_is_rejected(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _attachment_source(tmp_path, {"file_path": "2023/validation/missing.pdf"})


def test_gaia_image_attachment_requires_vision_model(tmp_path: Path):
    attachment = tmp_path / "2023" / "validation" / "label.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"image")
    run_root = tmp_path / "runs"
    run_root.mkdir()

    class NeverLLM:
        def generate(self, messages, tools):
            raise AssertionError("image validation should fail before invoking the LLM")

    with pytest.raises(RuntimeError, match="requires --vision-model"):
        _run_case(
            {"task_id": "task-image", "Question": "Read it", "file_path": "2023/validation/label.png"},
            tmp_path,
            NeverLLM(),
            12,
            run_root,
        )


def test_gaia_audio_attachment_is_locally_transcribed(monkeypatch, tmp_path: Path):
    attachment = tmp_path / "2023" / "validation" / "sample.mp3"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"audio")
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(gaia_eval, "transcribe_audio", lambda path, model: "[0.00-1.00] spoken evidence")

    class TranscriptLLM:
        def generate(self, messages, tools):
            assert "spoken evidence" in str(messages)
            return [AgentEvent(session_id="fake", type=AgentEventType.ASSISTANT_FINAL, payload={"text": "answer"})]

    result = _run_case(
        {"task_id": "task-audio", "Question": "Listen", "Final answer": "answer", "file_path": "2023/validation/sample.mp3"},
        tmp_path,
        TranscriptLLM(),
        12,
        run_root,
        audio_model="base.en",
    )

    assert result["correct"] is True


def test_gaia_image_attachment_is_preanalyzed_with_exact_question(tmp_path: Path):
    attachment = tmp_path / "2023" / "validation" / "sample.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"image")
    run_root = tmp_path / "runs"
    run_root.mkdir()
    prompts = []

    class FinalLLM:
        def generate(self, messages, tools):
            assert "visual evidence" in str(messages)
            return [AgentEvent(session_id="fake", type=AgentEventType.ASSISTANT_FINAL, payload={"text": "answer"})]

    def inspect(path, prompt):
        prompts.append(prompt)
        return "visual evidence"

    result = _run_case(
        {"task_id": "task-image", "Question": "Exact image question", "Final answer": "answer", "file_path": "2023/validation/sample.png"},
        tmp_path,
        FinalLLM(),
        12,
        run_root,
        vision_model="vision",
        vision_inspector=inspect,
    )

    assert result["correct"] is True
    assert "Exact image question" in prompts[0]


def test_gaia_case_runner_isolates_attachment_and_records_result(tmp_path: Path):
    attachment = tmp_path / "2023" / "validation" / "evidence.txt"
    attachment.parent.mkdir(parents=True)
    attachment.write_text("evidence", encoding="utf-8")
    run_root = tmp_path / "runs"
    run_root.mkdir()

    class FinalLLM:
        def generate(self, messages, tools):
            assert "evidence.txt" in str(messages)
            assert tools
            return [AgentEvent(session_id="fake", type=AgentEventType.ASSISTANT_FINAL, payload={"text": "FINAL ANSWER: 90"})]

    result = _run_case(
        {"task_id": "task-1", "Question": "How many?", "Final answer": "90", "file_path": "2023/validation/evidence.txt"},
        tmp_path,
        FinalLLM(),
        12,
        run_root,
    )

    assert result["correct"] is True
    assert result["attachment"] == "evidence.txt"
