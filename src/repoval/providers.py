"""Model providers.

A provider turns a prompt into text. Keeping this surface tiny (one method) is
what lets the same harness drive a deterministic oracle in CI, a shell script
wrapping some local model, or the Anthropic API.

Spec strings accepted by :func:`make_provider`:

  oracle               returns the gold patch; a correct harness must score 100%
  null                 returns nothing; a correct harness must score 0%
  sim:0.4              returns gold with p=0.4 else a plausible-looking wrong
                       patch; seeded, so runs reproduce. Handy for pass@k demos
  replay:RUN_DIR       replays completions recorded by an earlier run
  cmd:./model.sh       pipes the prompt to a shell command, reads stdout
  anthropic:MODEL      Messages API (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .models import Task
from .patching import split_by_file
from .prompts import Prompt


@dataclass
class Completion:
    text: str
    latency: float = 0.0
    usage: Dict[str, int] = field(default_factory=dict)


class Provider:
    name = "provider"

    def generate(self, task: Task, prompt: Prompt, sample: int) -> Completion:  # pragma: no cover
        raise NotImplementedError


def _fence(patch: str) -> str:
    return f"Here is the fix.\n\n```diff\n{patch.rstrip()}\n```\n"


def _rng(*parts: Any) -> random.Random:
    # hashlib, not hash(): str hashing is salted per process and would break reproducibility
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


class OracleProvider(Provider):
    name = "oracle"

    def generate(self, task, prompt, sample):
        return Completion(_fence(task.gold_patch))


class NullProvider(Provider):
    name = "null"

    def generate(self, task, prompt, sample):
        return Completion("I could not find the problem.")


class SimProvider(Provider):
    """A fake model with a known success rate, to sanity-check metrics end to end."""

    def __init__(self, p: float, seed: int = 0):
        self.p, self.seed = p, seed
        self.name = f"sim:{p}"

    def generate(self, task, prompt, sample):
        rng = _rng(self.seed, task.instance_id, sample)
        if rng.random() < self.p:
            return Completion(_fence(task.gold_patch))
        # A wrong answer, in one of the ways real models are wrong:
        mode = rng.choice(["truncated", "prose", "wrong_place", "edits_tests"])
        if mode == "prose":
            return Completion("I think the bug is in the parsing logic; you should review it.")
        files = split_by_file(task.gold_patch)
        if mode == "truncated":  # diff cut off mid-hunk: git apply rejects it
            return Completion(_fence(task.gold_patch[: max(40, len(task.gold_patch) // 2)]))
        if mode == "wrong_place":  # applies cleanly, fixes nothing: the classic "plausible but useless" patch
            note = ("--- /dev/null\n+++ b/NOTES.md\n@@ -0,0 +1,2 @@\n"
                    "+Possible fix: validate inputs earlier.\n+(not implemented)\n")
            return Completion(_fence(note))
        # edits_tests: tries to win by touching the tests, which the grader strips
        fake = ("--- a/tests/test_cheat.py\n+++ b/tests/test_cheat.py\n@@ -0,0 +1,2 @@\n"
                "+def test_cheat():\n+    assert True\n")
        return Completion(_fence(fake))


class ReplayProvider(Provider):
    def __init__(self, run_dir: Path):
        self.name = f"replay:{run_dir.name}"
        self._by_key: Dict[tuple, str] = {}
        for line in (Path(run_dir) / "completions.jsonl").read_text().splitlines():
            rec = json.loads(line)
            self._by_key[(rec["instance_id"], rec["sample"])] = rec["text"]

    def generate(self, task, prompt, sample):
        try:
            return Completion(self._by_key[(task.instance_id, sample)])
        except KeyError:
            raise LookupError(f"no recorded completion for {task.instance_id} #{sample}")


class CommandProvider(Provider):
    def __init__(self, command: str, timeout: int = 600):
        self.command, self.timeout = command, timeout
        self.name = f"cmd:{command}"

    def generate(self, task, prompt, sample):
        start = time.monotonic()
        proc = subprocess.run(
            self.command, shell=True, input=f"{prompt.system}\n\n{prompt.user}", text=True,
            capture_output=True, timeout=self.timeout,
            env={**os.environ, "REPOVAL_INSTANCE_ID": task.instance_id, "REPOVAL_SAMPLE": str(sample)},
        )
        if proc.returncode != 0:
            raise RuntimeError(f"command exited {proc.returncode}: {proc.stderr[-300:]}")
        return Completion(proc.stdout, latency=time.monotonic() - start)


class AnthropicProvider(Provider):
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, temperature: float = 0.7, max_tokens: int = 4096,
                 retries: int = 5, transport: Optional[Callable[[urllib.request.Request], bytes]] = None):
        self.model, self.temperature, self.max_tokens, self.retries = model, temperature, max_tokens, retries
        self.name = f"anthropic:{model}"
        self._transport = transport or self._http

    @staticmethod
    def _http(req: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.read()

    def generate(self, task, prompt, sample):
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key and self._transport == self._http:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        body = json.dumps({
            "model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature,
            "system": prompt.system, "messages": [{"role": "user", "content": prompt.user}],
        }).encode()
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        start = time.monotonic()
        for attempt in range(self.retries):
            try:
                raw = self._transport(urllib.request.Request(self.URL, body, headers))
                data = json.loads(raw)
                text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                usage = {k: v for k, v in data.get("usage", {}).items() if isinstance(v, int)}
                return Completion(text, time.monotonic() - start, usage)
            except urllib.error.HTTPError as e:
                if e.code not in (408, 409, 429, 500, 502, 503, 529) or attempt == self.retries - 1:
                    raise
            # exponential backoff with jitter; the sleep is injectable-free on purpose (tests use retries=1)
            time.sleep(min(30, 2 ** attempt) * (0.5 + random.random() / 2))
        raise RuntimeError("unreachable")


def make_provider(spec: str) -> Provider:
    kind, _, arg = spec.partition(":")
    if kind == "oracle":
        return OracleProvider()
    if kind == "null":
        return NullProvider()
    if kind == "sim":
        return SimProvider(float(arg or 0.5))
    if kind == "replay":
        return ReplayProvider(Path(arg))
    if kind == "cmd":
        return CommandProvider(arg)
    if kind == "anthropic":
        return AnthropicProvider(arg or "claude-sonnet-5-5")
    raise ValueError(f"unknown provider spec {spec!r}")
