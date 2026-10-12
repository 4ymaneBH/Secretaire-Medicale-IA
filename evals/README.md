# Conversation evaluations

This first evaluation layer compares recorded calls with 20 synthetic, labeled
scenarios in `scenarios.jsonl`. It grades observable tool actions and final
application state. The optional LLM judge reviews response wording separately.

## Capture and export

For new calls, `Transcript` stores both patient and assistant turns. The final
`Outcome.meta_data` contains a versioned event list with tool names, selected
non-free-text arguments, tool status, and whether Twilio accepted a transfer
redirect. It intentionally omits free-text tool reasons and memory queries.

Export one call to a JSONL file:

```powershell
python -m evals.export_call --call-sid CA... --scenario-id urgent_fr_01 --output evals/runs/urgent_fr_01.jsonl
```

Add `--include-text` only when the conversation text is needed for review.
Exported traces can contain health information; `evals/runs/` is gitignored.
The scenario ID is assigned by the evaluator, not inferred from a live call.

## Deterministic grading

```powershell
python -m evals.grade --runs evals/fixtures/runs.jsonl
python -m evals.grade --runs evals/runs/urgent_fr_01.jsonl --output evals/runs/report.json
```

The grader reports each check, pass counts by category, scenario coverage,
speech-end-to-first-audio-sent latency (p50/p95), initial stream-to-audio
latency, and TTS error count. Audio-send timing ends when the server sends a
chunk to Twilio; it does not measure when the caller actually hears it.
Its exit code is 1 when a captured run fails any deterministic check. Missing
scenarios are shown as coverage, not treated as passing runs.

Each scenario can require or forbid tools, require an action sequence, and
specify expected appointment, transfer, urgency, or language state. The
`final_state` comes from persisted state and the appointment row, rather than
from the assistant's spoken claim. `transfer_redirect_accepted` means Twilio
accepted the redirection request; it does **not** establish that a human
answered. The scenario set is a development regression set, not evidence of
clinical safety or real-call performance.

## Optional response judge

```powershell
python -m evals.judge --run evals/runs/urgent_fr_01.jsonl --model YOUR_JUDGE_MODEL --deidentified --output evals/runs/judgment.json
```

This sends the trace to the configured OpenAI API and incurs model usage.
`--deidentified` is an explicit gate: use only synthetic or properly
de-identified text. The judge scores groundedness, appointment consent,
clarity, and the boundary around medical advice. It may return `uncertain`;
its labels are not safety gates. Calibrate the rubric on human-reviewed calls,
especially urgent cases, before using aggregate judge scores.

Store independent reviewer labels as JSONL, for example:

```json
{"scenario_id":"urgent_fr_01","labels":{"groundedness":"pass","consent":"not_applicable","clarity":"pass","medical_boundary":"pass"}}
```

Then compare them with the JSONL files produced by `evals.judge`:

```powershell
python -m evals.calibrate_judge --reference evals/runs/reviewer_labels.jsonl --judgments evals/runs/judgments.jsonl
```

The report includes agreement, judge false passes, and abstentions per
dimension. Reference labels should come from a person reviewing the complete
record; emergency labels need an appropriately qualified reviewer.

## Next measurement layer

These traces contain text, tool outcomes, and timing markers, but no audio.
A voice evaluation should replay telephone-quality audio and assess
date/number recognition, audible quality, and interruption recovery. A
transfer-status callback or downstream telephony event is needed to measure
whether the forwarded human leg actually connected.
