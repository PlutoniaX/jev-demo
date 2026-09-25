"""Runs every control against every in-scope record and summarises the results."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from math import comb
from pathlib import Path
from statistics import median

from .controls import CONTROLS, EXCEPTION, PASS, POLICY_VERSION, REVIEW, SEVERITY, UNSURE_HIGH, UNSURE_LOW

PRICE_PER_M_INPUT = 0.042  # USD, TypeSafe list price for Jev input tokens
SAMPLE_SIZE = 25           # typical attribute sample for a quarterly control test
REVIEWER_RATE = 95         # USD per hour, fully loaded second-line tester


def _label_agrees(answer: dict, label) -> bool | None:
    """Does Jev's answer match the human label? None when the label is 'unsure'."""
    if label == "unsure" or label is None:
        return None
    t = answer["type"]
    if t == "noul":
        p = answer["noul"]
        if UNSURE_LOW < p < UNSURE_HIGH:
            return None  # Jev abstained; routed to a human, not counted as right or wrong
        return (p >= UNSURE_HIGH) == bool(label)
    if t == "score":
        return round(answer["score"]) == label
    return answer["choice"] == label


def run(population: dict, jev, workers: int = 8) -> tuple[dict, list[dict]]:
    records = population["records"]
    start = date.fromisoformat(population["start"])
    jobs = [(c, r) for c in CONTROLS for r in records if r["kind"] == c.applies_to]

    def test(job):
        control, record = job
        state = control.state(record)
        meta = {"control": control.id, "record_id": record["id"], "truth": record["truth"].get(control.id, {})}
        result = jev.ask(state, control.questions, meta)
        outcome = control.evaluate(result["answers"], record)
        return control, record, state, result, outcome

    with ThreadPoolExecutor(max_workers=workers) as pool:
        tested = list(pool.map(test, jobs))

    audit = []
    tests = []
    for control, record, state, result, outcome in tested:
        truth = record["truth"].get(control.id, {})
        agreement = {n: _label_agrees(a, truth.get(n)) for n, a in result["answers"].items()}
        tested_at = datetime.fromisoformat(record["timestamp"]).replace(hour=23, minute=50)
        entry = {
            "test_id": f"{control.id}:{record['id']}",
            "control": control.id,
            "record_id": record["id"],
            "record_kind": record["kind"],
            "day": record["day"],
            "event_at": record["timestamp"],
            "tested_at": tested_at.isoformat(timespec="minutes"),
            "status": outcome.status,
            "severity": SEVERITY[outcome.severity] if outcome.status == EXCEPTION else None,
            "reason": outcome.reason,
            "answers": result["answers"],
            "model": result["model"],
            "backend": jev.name,
            "policy_version": POLICY_VERSION,
            "state_sha256": hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
            "input_tokens": result["usage"].get("input_tokens") or 0,
            "latency_ms": result["latency_ms"],
            "agreement": agreement,
            "scenario": record["scenario"],
        }
        audit.append(entry)
        tests.append((entry, state))

    return summarise(population, tests, jev, start), audit


def _p_sample_detects(population: int, bad: int, n: int = SAMPLE_SIZE) -> float:
    n = min(n, population)
    if bad == 0:
        return 0.0
    return 1 - comb(population - bad, n) / comb(population, n)


def summarise(population: dict, tests: list, jev, start: date) -> dict:
    days = population["days"]
    by_control = defaultdict(list)
    for entry, _ in tests:
        by_control[entry["control"]].append(entry)

    rng = random.Random(population["seed"])
    controls = []
    for c in CONTROLS:
        entries = by_control[c.id]
        counts = Counter(e["status"] for e in entries)
        daily = [{"tested": 0, "exception": 0, "review": 0} for _ in range(days)]
        for e in entries:
            daily[e["day"]]["tested"] += 1
            if e["status"] != PASS:
                daily[e["day"]][e["status"]] += 1
        exceptions = counts[EXCEPTION]
        sample = rng.sample(entries, min(SAMPLE_SIZE, len(entries)))
        sample_found = sum(1 for e in sample if e["status"] == EXCEPTION)
        recent = daily[-7:]
        recent_rate = sum(d["exception"] for d in recent) / max(1, sum(d["tested"] for d in recent))
        rate = exceptions / max(1, len(entries))
        # Health is judged on the trailing week: red for any high-severity exception or a 5%+ exception
        # rate, amber for any exception at all, green otherwise.
        last_week = [e for e in entries if e["status"] == EXCEPTION and e["day"] >= days - 7]
        if recent_rate >= 0.05 or any(e["severity"] == "high" for e in last_week):
            status = "red"
        elif last_week:
            status = "amber"
        else:
            status = "green"
        controls.append({
            "id": c.id,
            "name": c.name,
            "system": c.system,
            "objective": c.objective,
            "frameworks": c.frameworks,
            "owner": c.owner,
            "questions": c.questions,
            "tested": len(entries),
            "passed": counts[PASS],
            "exceptions": exceptions,
            "review": counts[REVIEW],
            "exception_rate": round(rate, 4),
            "recent_exception_rate": round(recent_rate, 4),
            "status": status,
            "daily": daily,
            "sample": {
                "size": len(sample),
                "found": sample_found,
                "p_detect": round(_p_sample_detects(len(entries), exceptions), 3),
            },
            "manual_minutes": c.manual_minutes,
        })

    all_entries = [e for e, _ in tests]
    states = {e["test_id"]: s for e, s in tests}
    total = len(all_entries)
    status_counts = Counter(e["status"] for e in all_entries)
    tokens = sum(e["input_tokens"] for e in all_entries)
    latencies = [e["latency_ms"] for e in all_entries]
    manual_minutes = sum(c["tested"] * c["manual_minutes"] for c in controls)
    review_minutes = sum(c["review"] * c["manual_minutes"] for c in controls)

    agree = [v for e in all_entries for v in e["agreement"].values() if v is not None]

    incidents = []
    for inc in population["incidents"]:
        first = min((e for e in all_entries if e["control"] == inc["control"] and e["status"] == EXCEPTION and e["day"] >= inc["start_day"]),
                    key=lambda e: e["event_at"], default=None)
        onset = min((e for e in all_entries if e["control"] == inc["control"] and e["day"] >= inc["start_day"] and e["scenario"] in ("ungrounded", "proxy")),
                    key=lambda e: e["event_at"], default=None)
        affected = sum(1 for e in all_entries if e["control"] == inc["control"] and e["status"] == EXCEPTION and inc["start_day"] <= e["day"] <= inc["end_day"])
        lag_h = None
        if first and onset:
            lag_h = (datetime.fromisoformat(first["tested_at"]) - datetime.fromisoformat(onset["event_at"])).total_seconds() / 3600
        incidents.append({
            **inc,
            "start": (start + timedelta(days=inc["start_day"])).isoformat(),
            "end": (start + timedelta(days=inc["end_day"])).isoformat(),
            "first_exception": first["test_id"] if first else None,
            "detected_at": first["tested_at"] if first else None,
            "onset_at": onset["event_at"] if onset else None,
            "hours_to_detect": round(lag_h, 1) if lag_h is not None else None,
            "affected": affected,
        })

    # Next quarterly test: fieldwork after quarter end (30 Sep) + 2 weeks.
    quarter_end = date(start.year, ((start.month - 1) // 3 + 1) * 3, 1) + timedelta(days=32)
    quarter_end = quarter_end.replace(day=1) - timedelta(days=1)
    quarterly_report = quarter_end + timedelta(days=14)

    exceptions = sorted(
        (dict(e, state=states[e["test_id"]]) for e in all_entries if e["status"] in (EXCEPTION, REVIEW)),
        key=lambda e: (e["status"] != EXCEPTION, {"high": 0, "medium": 1, "low": 2, None: 3}[e["severity"]], e["event_at"]),
    )

    return {
        "bank": population["bank"],
        "window": {"start": population["start"], "end": (start + timedelta(days=population["days"] - 1)).isoformat(), "days": population["days"]},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": jev.name,
        "model": all_entries[0]["model"] if all_entries else None,
        "policy_version": POLICY_VERSION,
        "thresholds": {"unsure_low": UNSURE_LOW, "unsure_high": UNSURE_HIGH},
        "totals": {
            "records": len(population["records"]),
            "tests": total,
            "passed": status_counts[PASS],
            "exceptions": status_counts[EXCEPTION],
            "review": status_counts[REVIEW],
            "auto_decided_pct": round((total - status_counts[REVIEW]) / max(1, total), 4),
            "input_tokens": tokens,
            "cost_usd": round(tokens / 1e6 * PRICE_PER_M_INPUT, 4),
            "median_latency_ms": round(median(latencies), 0) if latencies else None,
            "manual_hours_equivalent": round(manual_minutes / 60, 1),
            "manual_cost_equivalent_usd": round(manual_minutes / 60 * REVIEWER_RATE),
            "review_hours": round(review_minutes / 60, 1),
            "agreement_pct": round(sum(agree) / max(1, len(agree)), 4),
            "agreement_n": len(agree),
            "sample_tests": sum(c["sample"]["size"] for c in controls),
            "sample_found": sum(c["sample"]["found"] for c in controls),
        },
        "assumptions": {
            "price_per_m_input_tokens_usd": PRICE_PER_M_INPUT,
            "sample_size": SAMPLE_SIZE,
            "reviewer_rate_usd_per_hour": REVIEWER_RATE,
            "quarterly_report_date": quarterly_report.isoformat(),
        },
        "controls": controls,
        "incidents": incidents,
        "exceptions": exceptions,
    }


def write_outputs(results: dict, audit: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=1))
    with (out_dir / "audit_log.jsonl").open("w") as f:
        for entry in audit:
            f.write(json.dumps(entry) + "\n")
