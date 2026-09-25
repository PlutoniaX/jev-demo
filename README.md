# Continuous AI control assurance with Jev

This demo shows a bank testing its AI controls on **every** AI decision, every night, rather than on a quarterly sample of 25 items. The judgement calls are made by [TypeSafe Jev](https://docs.typesafe.ai/introduction), a "System One" model: it returns typed answers with calibrated probabilities, not free text.

The bank, Fernhill Bank, N.A., is fictional. It runs two AI systems:

- **Penny**, a GenAI customer assistant.
- **LendScore**, an AI credit-decisioning model.

The demo covers 30 days of synthetic activity: 720 conversations, 240 credit decline notices and 9 AI change records. Two incidents are seeded into that month for the demo to catch.

Open `docs/index.html` in a browser to see the executive dashboard.

## The controls

| ID | Control | System | Jev questions | Mapped to |
|----|---------|--------|---------------|-----------|
| AI-01 | Assistant answers are grounded in approved content | Penny | Noul: is every claim supported by the retrieved knowledge base? Score: potential harm | UDAAP, NIST AI RMF, EU AI Act Art. 15 |
| AI-02 | Assistant does not give personal investment advice | Penny | Noul: is this a personal recommendation? | Reg BI, FINRA 2111 |
| AI-03 | Complaints and customers in hardship reach a person | Penny | Noul: complaint? Noul: hardship or vulnerability? Choice: topic | CFPB, FINRA 2165, EU AI Act Art. 14 |
| AI-04 | Credit decline reasons are specific, accurate and fair | LendScore | Noul: specific? Noul: consistent with model factors? Noul: protected characteristic or proxy? | ECOA / Reg B, CFPB Circular 2022-03 |
| AI-05 | AI model changes are validated before release | All | Score: materiality. Noul: independently validated before deployment? | SR 11-7, EU AI Act Art. 17 |

Each control lives in `ccassure/controls.py`. A control has two parts:

1. **The questions Jev answers.** These describe the judgement only.
2. **A policy function written in ordinary code.** It sets the thresholds and combines Jev's answers with system facts, such as whether the conversation was actually handed to a human. It then marks each item `pass`, `exception` or `review`.

Any probability between 25% and 75% means Jev is unsure, and the item goes to a person. Jev never signs off a control.

## The story the demo tells

- **INC-2 (9 Aug).** LendScore v4.2 was properly validated for accuracy. But its new area-default-rate feature leaked into decline reasons, so some notices told applicants their ZIP code counted against them. That is a fair-lending proxy. AI-04 flagged it the same day.
- **INC-1 (17 Aug).** A "friendlier tone" prompt change went live without validation, and AI-05 flagged the change record. The new prompt dropped the "answer only from retrieved content" rule, and Penny began quoting last year's mortgage and savings rates. AI-01 flagged it within hours.
- **The comparison.** A 25-item quarterly sample had roughly a coin-flip chance of finding any AI-01 exception, and would not report until mid-October.
- **The cost.** Testing all ~2,400 control instances costs about **$0.03** in Jev calls. The dashboard also shows the hours needed to test the same volume by hand.

### Five-minute talk track for executives

1. **Headline and KPI row.** "Every AI decision this month was tested, and 96% needed no person."
2. **Exceptions-by-day chart.** Point at the two shaded spikes.
3. **Incident cards.** Walk through start → detected → fixed, and the root cause in a model change. The point to land: good paperwork (INC-2 was validated) is not the same as good outcomes.
4. **Continuous vs quarterly sample panel.** Coverage and time-to-detect.
5. **Open one exception in the queue.** Show the evidence, the exact question Jev was asked, and the probability against the "unsure" band. The audit line shows the model, policy version and evidence hash, which is what an auditor or regulator would ask for.
6. **"How it works" guardrails.** Jev supplies probabilities, and the bank's policy and people make decisions.

## Running it

Requires Python 3.10+.

```bash
pip install -r requirements.txt

# Live: call Jev for every test and record the responses (needs your key)
export TYPESAFE_API_KEY=...
python -m ccassure --mode live

# Replay: rebuild the dashboard from recordings/ with no network or key
python -m ccassure --mode replay

# Simulated: Jev-shaped answers drawn from the test labels (no key needed)
python -m ccassure --mode sim
```

With no `--mode`, the run replays `recordings/jev-latest.jsonl` if it exists and simulates otherwise.

Every run writes three files:

- `docs/index.html`: the dashboard, with all data embedded, so it works offline.
- `out/results.json`: the summary data behind the dashboard.
- `out/audit_log.jsonl`: one line per test, with the answers, full distributions, model, policy version and evidence hash.

A live run of about 2,400 calls takes roughly a minute with the default 8 workers (`--workers`). That is well inside Jev's published rate limits.

**The committed dashboard uses simulated answers,** and it says so in its header and footer. They were generated in an environment that could not reach `api.typesafe.ai`. Before showing it to anyone, run `--mode live` once. Then commit `recordings/`, `out/` and `docs/`, and the demo will replay real Jev output from then on.

### Checking Jev against human labels

Every synthetic record carries the label a careful tester would give. This label is never sent to Jev. The "agreement with testers" KPI compares Jev's clear-cut answers with those labels, and items where Jev abstained are left out of the count. Where the label disagrees with an exception, the exception queue shows a note on the item.

In simulated mode this figure is made up. In live mode it is a real measure of Jev on this control set, and it is the number to tune thresholds and question wording against.

## Layout

```
ccassure/controls.py     control library: Jev questions + decision policy
ccassure/population.py   synthetic Fernhill Bank evidence and seeded incidents
ccassure/jev.py          Live (typesafe-sdk), Replay and Simulated backends
ccassure/engine.py       runs every control on every record, sampling comparison, audit log
ccassure/report.py       embeds results into dashboard/template.html -> docs/index.html
tests/                   policy and pipeline tests (pytest)
```

## Adapting it

- **Add a control.** Append a `Control` in `controls.py` with its questions, a `state()` function that selects the evidence fields, and an `evaluate()` policy.
- **Use real evidence.** Replace `population.py` with an extract from your conversation logs, adverse action notice store or change management system. Leave out the `truth` field, or fill it from a QA sample so the agreement figure stays meaningful.
- **Tune the thresholds.** Adjust `UNSURE_LOW`, `UNSURE_HIGH` and `MIN_CONFIDENCE` using the stored distributions in the audit log.
