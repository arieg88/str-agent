"""Disk cache for verified memos, to save money while developing.

One JSON file per key under <cache_dir>/memo/. The key covers everything that can change the
answer: the facts (their hash includes FACTS_VERSION), the prompt version and text, provider,
model, base_url, effort and the output limit. A hit is re-validated by the caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from stragent.config import LLMConfig
from stragent.llm.prompts import MemoPrompt
from stragent.models import Memo

log = logging.getLogger("stragent.memo")


def cache_key(
    facts_hash: str, prompt: MemoPrompt, cfg: LLMConfig, provider: str, model: str
) -> str:
    parts = {
        "facts": facts_hash,
        "prompt_version": prompt.version,
        "prompt_text": prompt.fingerprint,
        "provider": provider,
        "model": model,
        "base_url": cfg.base_url,
        "effort": cfg.effort,
        "max_output_tokens": cfg.max_output_tokens,
    }
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class MemoCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Memo | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return Memo.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            log.warning("ignoring unreadable memo cache entry %s: %s", path.name, exc)
            return None

    def put(self, key: str, memo: Memo) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(memo.model_dump_json(), encoding="utf-8")
        tmp.replace(self._path(key))
