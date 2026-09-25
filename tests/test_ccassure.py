import json

from ccassure import engine, population
from ccassure.controls import AI03, AI04, AI05, EXCEPTION, PASS, REVIEW
from ccassure.jev import ReplayJev, SimulatedJev, request_key


def noul(p):
    return {"type": "noul", "noul": p}


def test_escalation_uses_system_fact_not_just_jev():
    answers = {"complaint": noul(0.95), "hardship_or_vulnerability": noul(0.02), "topic": {"type": "choice", "choice": "lending", "confidence": 0.9}}
    assert AI03.evaluate(answers, {"escalated": False}).status == EXCEPTION
    assert AI03.evaluate(answers, {"escalated": True}).status == PASS


def test_unsure_answers_go_to_review():
    answers = {"specific_reasons": noul(0.5), "consistent_with_model": noul(0.9), "protected_characteristic": noul(0.05)}
    assert AI04.evaluate(answers, {}).status == REVIEW


def test_protected_proxy_is_high_severity():
    answers = {"specific_reasons": noul(0.95), "consistent_with_model": noul(0.9), "protected_characteristic": noul(0.97)}
    outcome = AI04.evaluate(answers, {})
    assert outcome.status == EXCEPTION and outcome.severity == 2


def test_material_unvalidated_change_is_exception():
    answers = {"materiality": {"type": "score", "score": 1.9, "confidence": 0.9}, "independently_validated": noul(0.03)}
    assert AI05.evaluate(answers, {}).status == EXCEPTION


def test_simulated_run_detects_both_incidents_same_day():
    results, audit = engine.run(population.generate(seed=7), SimulatedJev(), workers=4)
    assert results["totals"]["tests"] == len(audit)
    for inc in results["incidents"]:
        assert inc["hours_to_detect"] is not None and inc["hours_to_detect"] < 24
    assert results["totals"]["agreement_pct"] > 0.95


def test_replay_reads_recordings(tmp_path):
    state, questions = {"a": 1}, {"q": {"type": "noul"}}
    rec = tmp_path / "rec.jsonl"
    row = {"key": request_key("jev-latest", state, questions), "answers": {"q": noul(0.9)}, "model": "jev-1.13.0",
           "usage": {"input_tokens": 10, "output_tokens": 0}, "latency_ms": 120.0}
    rec.write_text(json.dumps(row) + "\n")
    result = ReplayJev(rec).ask(state, questions, {})
    assert result["answers"]["q"]["noul"] == 0.9 and result["model"] == "jev-1.13.0"
