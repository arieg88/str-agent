"""Versioned prompt files (src/stragent/prompts/memo/v1.md) and the text that fills them.

Only text produced by this code reaches the model: the fact list and fixed sentences chosen by
warning code. Never the typed address, the geocoder's display name or listing names, so a
listing title cannot inject instructions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from string import Template

from stragent.llm.facts import fact_lines
from stragent.models import Analysis, Fact, MemoProblem, MemoSections

# Analysis.warning_codes -> what the model is told. A code without an entry is left out.
WARNING_TEXT: dict[str, str] = {
    "few_comps": "Fewer comparable listings were found than the search aims for, so the ranges "
    "are rough.",
    "radius_widened": "The search radius was widened to find enough comparable listings.",
    "few_active": "Many comps had no reviews in the last 12 months, so the estimates rest on few "
    "listings.",
    "occupancy_capped": "Many active comps hit the occupancy cap, so the top of the revenue range "
    "may be understated.",
    "outside_neighbourhoods": "The address is outside the mapped neighbourhoods.",
    "synthetic_data": "SYNTHETIC TEST DATA: say plainly in the verdict that these are not real "
    "market figures.",
}
NO_WARNINGS = "None."


@dataclass(frozen=True)
class MemoPrompt:
    version: str
    system: str
    user: Template
    retry: Template

    @property
    def fingerprint(self) -> str:
        """Changes whenever the prompt text changes, even if the version was not bumped."""
        text = "\n".join([self.system, self.user.template, self.retry.template])
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load_prompt(version: str) -> MemoPrompt:
    path = resources.files("stragent.prompts").joinpath("memo", f"{version}.md")
    text = path.read_text(encoding="utf-8")
    parts: dict[str, str] = {}
    for chunk in text.split("<!-- ")[1:]:
        marker, _, body = chunk.partition(" -->")
        parts[marker.strip()] = body.strip("\n")
    missing = {"system", "user", "retry"} - parts.keys()
    if missing:
        raise ValueError(f"Prompt memo/{version}.md lacks sections: {', '.join(sorted(missing))}")
    return MemoPrompt(version, parts["system"], Template(parts["user"]), Template(parts["retry"]))


def warning_lines(analysis: Analysis) -> str:
    lines = [
        f"- {WARNING_TEXT[c]}" for c in dict.fromkeys(analysis.warning_codes) if c in WARNING_TEXT
    ]
    return "\n".join(lines) or NO_WARNINGS


def _schema() -> str:
    return json.dumps(MemoSections.model_json_schema(), indent=1)


def user_message(prompt: MemoPrompt, analysis: Analysis, facts: list[Fact]) -> str:
    return prompt.user.substitute(
        facts=fact_lines(facts), warnings=warning_lines(analysis), schema=_schema()
    )


def retry_message(
    prompt: MemoPrompt,
    analysis: Analysis,
    facts: list[Fact],
    previous: MemoSections,
    problems: list[MemoProblem],
) -> str:
    return prompt.retry.substitute(
        facts=fact_lines(facts),
        warnings=warning_lines(analysis),
        previous=previous.model_dump_json(indent=1),
        problems="\n".join(f"- [{p.section}] {p.detail}" for p in problems),
        schema=_schema(),
    )
