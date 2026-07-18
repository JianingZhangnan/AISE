import json
import sys
from pathlib import Path

from phycode.llm import ScriptedLLM


def write_public_task_files(workspace: Path, *, approvals: bool = True) -> tuple[Path, Path]:
    (workspace / "instruction.md").write_text(
        "Create reproduce.py, run it, and produce result.csv with message=hello.\n",
        encoding="utf-8",
    )
    (workspace / "paper.md").write_text(
        "Public paper: the expected greeting is hello.\n",
        encoding="utf-8",
    )
    contract_path = workspace / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "instruction_file": "instruction.md",
                "paper_file": "paper.md",
                "expected_files": ["reproduce.py", "result.csv"],
                "constraints": [
                    {
                        "path": "result.csv",
                        "csv_header": ["message"],
                        "csv_rows": [["hello"]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    approvals_path = workspace / "approvals.json"
    grants: list[dict[str, object]] = []
    if approvals:
        grants = [
            {"tool_name": "file.write", "path": "reproduce.py"},
            {
                "tool_name": "process.run",
                "argv": [sys.executable, "reproduce.py"],
                "cwd": ".",
            },
        ]
    approvals_path.write_text(json.dumps({"grants": grants}), encoding="utf-8")
    return contract_path, approvals_path


def write_text_task_files(
    workspace: Path,
    *,
    grants: list[dict[str, object]] | None = None,
) -> tuple[Path, Path]:
    (workspace / "instruction.md").write_text("Create result.txt.\n", encoding="utf-8")
    (workspace / "paper.md").write_text("Public supporting text.\n", encoding="utf-8")
    contract_path = workspace / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "instruction_file": "instruction.md",
                "paper_file": "paper.md",
                "expected_files": ["result.txt"],
            }
        ),
        encoding="utf-8",
    )
    approvals_path = workspace / "approvals.json"
    approvals_path.write_text(
        json.dumps({"grants": grants or []}),
        encoding="utf-8",
    )
    return contract_path, approvals_path


def scripted_llm_that_writes_runs_reads_and_finishes() -> ScriptedLLM:
    script = (
        "from pathlib import Path\n"
        "Path('result.csv').write_text('message\\nhello\\n', encoding='utf-8')\n"
    )
    return ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "reproduce.py", "content": script},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "process.run",
                        "args": {"argv": [sys.executable, "reproduce.py"], "cwd": "."},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": "result.csv"}},
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )


class RecordingFinalLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools):
        del messages, tools
        self.calls += 1
        return ScriptedLLM(
            [[{"type": "assistant_final", "payload": {"text": "done"}}]]
        ).generate([], [])


class RaisingLLM:
    def generate(self, messages, tools):
        del messages, tools
        raise RuntimeError(
            "sk-provider-crash-123456789 https://private.example/v1 "
            "argv=[secret-approval-argument]"
        )
