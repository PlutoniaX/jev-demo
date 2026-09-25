"""Command line entry point.

    python -m ccassure                 # replay recordings if present, otherwise simulate
    python -m ccassure --mode live     # call Jev (needs TYPESAFE_API_KEY), record responses
    python -m ccassure --mode replay   # re-run from recordings, no network
    python -m ccassure --mode sim      # simulated Jev answers
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import engine, population, report
from .jev import DEFAULT_MODEL, LiveJev, ReplayJev, SimulatedJev

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(prog="ccassure", description="Continuous AI control assurance with Jev")
    ap.add_argument("--mode", choices=["auto", "live", "replay", "sim"], default="auto")
    ap.add_argument("--model", default=os.environ.get("TYPESAFE_DEFAULT_MODEL", DEFAULT_MODEL))
    ap.add_argument("--seed", type=int, default=7, help="seed for the synthetic population")
    ap.add_argument("--workers", type=int, default=8, help="concurrent Jev requests in live mode")
    ap.add_argument("--regenerate", action="store_true", help="rebuild data/population.json")
    args = ap.parse_args()

    pop_path = ROOT / "data" / "population.json"
    rec_path = ROOT / "recordings" / f"{args.model}.jsonl"
    if args.regenerate or not pop_path.exists():
        pop = population.write(pop_path, args.seed)
    else:
        pop = json.loads(pop_path.read_text())

    mode = args.mode
    if mode == "auto":
        mode = "replay" if rec_path.exists() else "sim"
    if mode == "live":
        if not os.environ.get("TYPESAFE_API_KEY"):
            raise SystemExit("Set TYPESAFE_API_KEY to run in live mode.")
        if rec_path.exists():
            rec_path.unlink()  # a live run replaces the previous recording
        jev = LiveJev(rec_path, args.model)
    elif mode == "replay":
        jev = ReplayJev(rec_path, args.model)
    else:
        jev = SimulatedJev()

    print(f"Testing {len(pop['records'])} records against 5 controls with Jev ({jev.name})...")
    results, audit = engine.run(pop, jev, workers=args.workers)
    engine.write_outputs(results, audit, ROOT / "out")
    report.build(results, ROOT / "dashboard" / "template.html", ROOT / "docs" / "index.html")

    t = results["totals"]
    print(f"  {t['tests']} tests | {t['exceptions']} exceptions | {t['review']} to human review | "
          f"{t['auto_decided_pct']:.1%} decided automatically")
    print(f"  Jev agreed with the human label on {t['agreement_pct']:.1%} of {t['agreement_n']} clear-cut judgements")
    print(f"  {t['input_tokens']:,} input tokens = ${t['cost_usd']:.4f}; median latency {t['median_latency_ms']:.0f} ms")
    for inc in results["incidents"]:
        print(f"  {inc['id']} {inc['title']}: detected {inc['hours_to_detect']} h after onset")
    print("Dashboard: docs/index.html   Results: out/results.json   Audit log: out/audit_log.jsonl")


if __name__ == "__main__":
    main()
