# CroweLM Quality Stack Eval - 2026-05-11

**First run: no prior baselines exist. Baselines established.**

## Header

| | Score |
|---|---|
| This week aggregate | 0.338 |
| Last week aggregate | n/a |
| Delta | n/a (first run) |

Aggregate = mean of per-transcript aggregates (0=perfect, 1=catastrophic).

## Per-Transcript Table

| transcript_id | this_week | last_week | delta | verdict |
|---|---|---|---|---|
| 2026-04-30-eclipse-email-blast | 0.577 | n/a | n/a | first run |
| 2026-04-30-talon-parallel-agent | 0.100 | n/a | n/a | first run |

## Cross-Variant Red Metrics

Metrics scoring above 0.6 in 2 or more transcripts:

| metric | transcripts above 0.6 | count |
|---|---|---|
| QS-11 | eclipse-email-blast (1.000), talon-parallel-agent (1.000) | 2 |

QS-11 is TTFT latency. Both transcripts exceed the 30000ms alert threshold (eclipse: 1095800ms, talon: 337000ms).

## New Transcripts

Both transcripts are new (no prior baseline):

- 2026-04-30-eclipse-email-blast
- 2026-04-30-talon-parallel-agent
