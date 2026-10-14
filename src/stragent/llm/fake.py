"""A scripted provider for tests and for the offline eval path: no network, no key."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from stragent.llm.provider import T
from stragent.models import LLMUsage


@dataclass
class Call:
    system: str
    user: str
    timeout: float


@dataclass
class ScriptedProvider:
    """Returns the queued answers in order. An exception in the queue is raised instead."""

    answers: list[BaseModel | Exception]
    name: str = "fake"
    model: str = "scripted"
    usage: LLMUsage = field(default_factory=lambda: LLMUsage(input_tokens=1000, output_tokens=500))
    calls: list[Call] = field(default_factory=list)

    def generate(
        self, system: str, user: str, schema: type[T], timeout: float
    ) -> tuple[T, LLMUsage]:
        self.calls.append(Call(system, user, timeout))
        if not self.answers:
            raise AssertionError("ScriptedProvider ran out of answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return schema.model_validate(answer.model_dump()), self.usage
