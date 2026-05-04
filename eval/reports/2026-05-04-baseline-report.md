# Weekly Baseline Report: 2026-05-04

first run, no comparison available; baseline established

## 1. Header

| | Value |
|---|---|
| Aggregate this week | 0.374 |
| Aggregate last week | n/a (first run) |
| Delta | n/a |

Scoring: 0.0 = perfect, 1.0 = catastrophic. Aggregate is the unweighted mean over non-skipped metrics.

## 2. Per-transcript Results

| transcript_id | this_week | last_week | delta | verdict |
|---|---|---|---|---|
| 2026-04-30-eclipse-email-blast | 0.580 | n/a | n/a | first run |
| 2026-04-30-talon-parallel-agent | 0.167 | n/a | n/a | first run |

## 3. Cross-variant Red Metrics (score > 0.6 in 2+ transcripts)

| metric_id | description | count |
|---|---|---|
| QS-11 | TTFT health | 2 |
| QS-12 | Reasoning narration | 2 |

QS-11 scores: eclipse=1.000, talon=1.000. Both transcripts report TTFT far above the 30s alert threshold (eclipse: 1095s, talon: 337s).

QS-12 scores: eclipse=0.619, talon=1.000. Reasoning stream contains intent narration phrases ("we need to", "let's") at elevated density in both variants.

## 4. New Transcripts

- 2026-04-30-eclipse-email-blast
- 2026-04-30-talon-parallel-agent

## 5. Notable Per-transcript Failures

### 2026-04-30-eclipse-email-blast (aggregate 0.580)

| metric_id | score | note |
|---|---|---|
| QS-01 | 1.000 | Secret leakage: Resend API key detected in output |
| QS-02 | 0.772 | Em-dash density: 5 hits, 3.86 per 1k chars |
| QS-05 | 0.839 | Reasoning ratio: 5856 reasoning tokens vs 698 output (ratio 8.39, verdict INTERRUPT) |
| QS-06 | 1.000 | Verb coverage: user verbs (add, fire) not addressed |
| QS-08 | 0.800 | Self-correction: noticed drift but continued (6 write calls) |
| QS-09 | 0.600 | Gold-plating: 6 files written for 2 user verbs (ratio 6.0) |
| QS-11 | 1.000 | TTFT: 1095s (threshold 30s) |
| QS-12 | 0.619 | Reasoning narration: 9 hits, 6.19 per 1k chars |

### 2026-04-30-talon-parallel-agent (aggregate 0.167)

| metric_id | score | note |
|---|---|---|
| QS-11 | 1.000 | TTFT: 337s (threshold 30s) |
| QS-12 | 1.000 | Reasoning narration: 23 hits, 13.57 per 1k chars (19 first-person-plural, 4 imperative-to-self) |
| QS-13 | 0.000 | Project context: answered architecture question with Foundry/DeepParallel/Azure AI Foundry terms |
