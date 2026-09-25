"""Jev backends.

* LiveJev      calls TypeSafe's System One API through the official `typesafe-sdk`
               and records every response so the demo can be replayed offline.
* ReplayJev    answers from those recordings (no network, no key).
* SimulatedJev produces Jev-shaped answers from the synthetic labels so the demo
               runs anywhere. The dashboard labels these runs as simulated.

All backends return answers as plain dicts in the API's wire shape:
  noul   -> {"type": "noul", "noul": 0.97}
  choice -> {"type": "choice", "choice": "x", "confidence": 0.9, "probabilities": {...}}
  score  -> {"type": "score", "score": 1.7, "confidence": 0.8, "probabilities": {0: .., 1: .., 2: ..}}
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "jev-latest"


def request_key(model: str, state: Any, questions: dict) -> str:
    blob = json.dumps({"model": model, "state": state, "questions": questions}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class Result(dict):
    """answers + usage + latency for one System One call."""


class LiveJev:
    name = "live"

    def __init__(self, recordings: Path, model: str = DEFAULT_MODEL):
        try:
            from typesafe_sdk import TypeSafeClient
        except ImportError as e:  # pragma: no cover
            raise SystemExit("Live mode needs the SDK: pip install -r requirements.txt") from e
        self.client = TypeSafeClient()  # reads TYPESAFE_API_KEY
        self.model = model
        self.recordings = recordings
        self.recordings.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def ask(self, state: Any, questions: dict, meta: dict) -> Result:
        started = time.perf_counter()
        resp = self.client.system_one(state=state, questions=questions, model=self.model)
        latency_ms = (time.perf_counter() - started) * 1000
        answers = {name: a.model_dump(mode="json") for name, a in resp.answers.items()}
        result = Result(
            answers=answers,
            model=resp.model,
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
            latency_ms=round(latency_ms, 1),
        )
        line = {"key": request_key(self.model, state, questions), **meta, **result, "recorded_at": datetime.now(timezone.utc).isoformat()}
        with self._lock, self.recordings.open("a") as f:
            f.write(json.dumps(line) + "\n")
        return result


class ReplayJev:
    name = "replay"

    def __init__(self, recordings: Path, model: str = DEFAULT_MODEL):
        self.model = model
        self.cache: dict[str, dict] = {}
        for line in recordings.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                self.cache[row["key"]] = row

    def ask(self, state: Any, questions: dict, meta: dict) -> Result:
        row = self.cache.get(request_key(self.model, state, questions))
        if row is None:
            raise KeyError(f"No recording for {meta}. Re-run in live mode to record it.")
        return Result(answers=row["answers"], model=row["model"], usage=row["usage"], latency_ms=row["latency_ms"])


class SimulatedJev:
    """Stand-in for Jev when there is no API access.

    Answers are drawn around the human label for each question: mostly confident
    when the label is clear, near 0.5 when the label is 'unsure', and a small rate
    of answers leaning the wrong way so the agreement figures are not perfect.
    """

    name = "simulated"
    ERROR_RATE = 0.006

    def __init__(self, model: str = "jev-latest (simulated)"):
        self.model = model

    def ask(self, state: Any, questions: dict, meta: dict) -> Result:
        truth = meta["truth"]
        rng = random.Random(f"{meta['control']}:{meta['record_id']}:{request_key(self.model, state, questions)}")
        answers = {}
        for name, q in questions.items():
            label = truth.get(name)
            if q["type"] == "noul":
                answers[name] = {"type": "noul", "noul": round(self._noul(rng, label), 4)}
            elif q["type"] == "score":
                answers[name] = self._score(rng, label if isinstance(label, int) else 1, len(q["criteria"]), q["criteria"])
            else:
                answers[name] = self._choice(rng, label, list(q["criteria"]))
        tokens = len(json.dumps(state)) // 4 + len(json.dumps(questions)) // 4
        return Result(answers=answers, model=self.model, usage={"input_tokens": tokens, "output_tokens": 0},
                      latency_ms=round(rng.uniform(70, 420), 1))

    def _noul(self, rng: random.Random, label: Any) -> float:
        if label == "unsure" or label is None:
            return rng.uniform(0.32, 0.68)
        if rng.random() < self.ERROR_RATE:
            # Wrong answers lean the wrong way but are rarely extreme, as with a calibrated model.
            x = rng.betavariate(4, 1.5)
            return 1 - x if label else x
        x = rng.betavariate(14, 1.1)  # mostly near 1, with a thin tail into the unsure band
        return x if label else 1 - x

    def _score(self, rng: random.Random, level: int, n: int, criteria: list) -> dict:
        weights = [rng.uniform(0.0, 0.08) for _ in range(n)]
        weights[level] = rng.uniform(0.75, 0.95)
        total = sum(weights)
        probs = {i: round(w / total, 4) for i, w in enumerate(weights)}
        return {
            "type": "score",
            "score": round(sum(i * p for i, p in probs.items()), 3),
            "confidence": round(max(probs.values()), 3),
            "probabilities": probs,
            "legend": {i: c for i, c in enumerate(criteria)},
        }

    def _choice(self, rng: random.Random, label: Any, labels: list[str]) -> dict:
        label = label if label in labels else "other"
        weights = {l: rng.uniform(0.0, 0.06) for l in labels}
        weights[label] = rng.uniform(0.7, 0.95)
        total = sum(weights.values())
        probs = {l: round(w / total, 4) for l, w in weights.items()}
        return {"type": "choice", "choice": label, "confidence": round(probs[label], 3), "probabilities": probs}
