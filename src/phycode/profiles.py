from __future__ import annotations

from dataclasses import dataclass

from phycode.context import CODING_SYSTEM_PROMPT, GAIA_SYSTEM_PROMPT
from phycode.models import AgentProfile

PRBENCH_SYSTEM_PROMPT = """You are PhyCode reproducing a public PRBench task.
Use only visible workspace inputs. Generate data by running reproduction scripts.
Inspect required artifacts before finishing; final is accepted only after artifact verification."""

_CODING_TOOL_NAMES = frozenset(
    {
        "calculator.calculate",
        "config.read",
        "config.write",
        "file.edit",
        "file.inspect",
        "file.list",
        "file.read",
        "file.write",
        "image.inspect",
        "keys.status",
        "memory.read",
        "memory.write",
        "search.glob",
        "search.grep",
        "shell.run",
        "test.run",
        "web.fetch",
        "web.search",
        "workspace.status",
    }
)
_GAIA_TOOL_NAMES = frozenset(
    {
        "calculator.calculate",
        "file.inspect",
        "file.list",
        "file.read",
        "image.inspect",
        "web.fetch",
        "web.search",
    }
)
_PRBENCH_TOOL_NAMES = frozenset(
    {
        "calculator.calculate",
        "file.edit",
        "file.inspect",
        "file.list",
        "file.read",
        "file.write",
        "image.inspect",
        "process.run",
        "search.glob",
        "search.grep",
        "workspace.status",
    }
)


@dataclass(frozen=True)
class ProfileSpec:
    profile: AgentProfile
    tool_names: frozenset[str]
    system_prompt: str
    max_context_chars: int
    max_tool_calls: int
    hidden_path_components: frozenset[str] = frozenset()


_PROFILE_SPECS = {
    AgentProfile.CODING: ProfileSpec(
        profile=AgentProfile.CODING,
        tool_names=_CODING_TOOL_NAMES,
        system_prompt=CODING_SYSTEM_PROMPT,
        max_context_chars=12_000,
        max_tool_calls=40,
    ),
    AgentProfile.GAIA: ProfileSpec(
        profile=AgentProfile.GAIA,
        tool_names=_GAIA_TOOL_NAMES,
        system_prompt=GAIA_SYSTEM_PROMPT,
        max_context_chars=24_000,
        max_tool_calls=12,
    ),
    AgentProfile.PRBENCH: ProfileSpec(
        profile=AgentProfile.PRBENCH,
        tool_names=_PRBENCH_TOOL_NAMES,
        system_prompt=PRBENCH_SYSTEM_PROMPT,
        max_context_chars=12_000,
        max_tool_calls=40,
        hidden_path_components=frozenset({"_ground_truth"}),
    ),
}


def profile_spec(profile: AgentProfile) -> ProfileSpec:
    return _PROFILE_SPECS[profile]
