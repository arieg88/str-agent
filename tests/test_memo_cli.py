"""`stragent memo` end to end on the synthetic DB, with a scripted provider (no network)."""

import json

import pytest
from typer.testing import CliRunner

import stragent.cli as cli
from conftest import CENTRE, CoordsOnly
from stragent.analysis import analyze
from stragent.db import connect
from stragent.llm.fake import ScriptedProvider
from stragent.llm.provider import LLMError, LLMUnavailableError
from stragent.models import Memo, MemoSections

pytestmark = pytest.mark.integration

WORDS_ONLY = MemoSections(
    verdict="Synthetic data: no real conclusion can be drawn.",
    pricing="Prices sit in a narrow band.",
    occupancy_revenue="Occupancy is inferred from reviews.",
    competition="The area is busy.",
    risks="1. The data is synthetic.",
    checks_before_buying="1. Load a real snapshot.",
)


@pytest.fixture
def run_memo(monkeypatch, db):
    monkeypatch.setattr(cli, "load_env", lambda: None)  # never read a developer's .env in tests

    def run(provider_or_error, *args):
        def factory(settings):
            if isinstance(provider_or_error, Exception):
                raise provider_or_error
            return provider_or_error

        monkeypatch.setattr(cli, "provider_factory", factory)
        return CliRunner().invoke(
            cli.app, ["memo", CENTRE, "--bedrooms", "2", "--no-cache", "--db", str(db), *args]
        )

    return run


def test_memo_command_prints_the_memo_and_the_cost_line(run_memo):
    result = run_memo(ScriptedProvider([WORDS_ONLY], model="claude-haiku-5-5"))
    assert result.exit_code == 0, result.output
    assert "## Verdict" in result.output and "Synthetic data" in result.output
    assert "Verified" in result.output
    flat = " ".join(result.output.split())  # rich wraps long lines
    assert "claude-haiku-5-5" in flat and "memo cost $0.00035" in flat


def test_memo_command_json(run_memo):
    result = run_memo(ScriptedProvider([WORDS_ONLY]), "--json")
    assert result.exit_code == 0, result.output
    memo = Memo.model_validate(json.loads(result.output[result.output.index("{") :]))
    assert memo.verified and memo.snapshot_date == "2026-09-25"


def test_memo_command_shows_problems_and_keeps_the_tags(run_memo):
    bad = WORDS_ONLY.model_copy(update={"pricing": "Comps charge €999 [F17] a night."})
    result = run_memo(ScriptedProvider([bad, bad]))
    assert result.exit_code == 0
    flat = " ".join(result.output.split())
    assert "Comps charge €999 [F17] a night." in flat  # the memo text, tags intact
    assert "NOT verified" in flat and "[pricing] '€999' does not match" in flat
    assert "[F17] Nightly price of comps: median" in flat


def test_memo_command_without_a_key(run_memo):
    result = run_memo(LLMUnavailableError("ANTHROPIC_API_KEY is not set. See [llm] in settings."))
    assert result.exit_code == 3
    assert "ANTHROPIC_API_KEY is not set. See [llm] in settings." in " ".join(result.output.split())


def test_memo_command_provider_failure(run_memo):
    result = run_memo(ScriptedProvider([LLMError("Anthropic did not answer in time.")]))
    assert result.exit_code == 4
    assert "did not answer in time" in result.output


def test_analysis_gives_a_code_for_every_warning(db, settings):
    con = connect(db, read_only=True)
    try:
        analysis = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), bedrooms=2)
    finally:
        con.close()
    assert len(analysis.warning_codes) == len(analysis.warnings)
    assert "synthetic_data" in analysis.warning_codes
