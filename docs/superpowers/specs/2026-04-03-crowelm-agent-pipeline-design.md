# CroweLM Agent-Driven Data Pipeline -- Design Spec

## Overview

Build an agent-driven pipeline for evaluating, generating, and curating CroweLM training data. Inspired by GitHub's agent-driven development principles (agents as primary contributors, blame process not agents) and GitHub Agentic Workflows security architecture (isolation, staged writes, logging at every boundary).

Three sub-projects, built and shipped independently in order:

1. **Agent Infrastructure** -- secure execution, staging pipeline, audit logging
2. **Evaluation Agents** -- coverage analysis, quality scoring, batch auditing
3. **Generation Agents** -- persona-based example generation, mycology first

## Current State

- 145,097 training examples (4.29 GB) in `data/crowelm-unified/`
- 137,875 NVIDIA biotech (genes, RNA, proteins) -- bulk of the data
- 2,222 original CroweLM mycology examples -- the differentiator
- 5,000 synthetic reasoning examples
- 10 existing tools in `tools/crowelm.py` (query, curation, pipeline tiers)
- 4 domain personas: mycology_expert, pharma_researcher, bioprocess_engineer, scientific_coder
- NeMo training config targeting RunPod (3 epochs, bf16-mixed, lr 1e-5)

## Architecture

### Data Flow

```
Generation Agent -> Staged Writes -> Evaluation Agent (scoring) -> Tiered Gate
                                                                    |
                                              Auto-approve (>0.85) -> dataset
                                              Review queue (0.5-0.85) -> human
                                              Auto-reject (<0.5) -> log + discard
```

### Key Principle

Agents never write directly to `data/crowelm-unified/curated/`. Everything goes through a staging area, gets evaluated, and only lands in the dataset after passing the tiered gate.

### Deployment

- **Local (dev):** Docker containers on Mac, dataset mounted read-only, staging directory writable
- **Production:** GitHub Actions with agentic workflow security model, approved examples merge via PR

---

## Sub-Project 1: Agent Infrastructure

### Components

**Secure Agent Runner** (`tools/agent_runner.py`)
- Launches agent tasks in Docker containers locally, or dispatches to GitHub Actions
- Mounts dataset as read-only inside agent containers
- Writes go to a staging directory (`data/crowelm-unified/staging/`)
- Agent containers have no access to `.env`, API keys, or git credentials
- LLM calls route through a proxy process that holds the tokens

**Staging Pipeline** (`tools/staging_pipeline.py`)
- Receives staged writes from agents (new examples, deletions, modifications)
- Each staged item gets a UUID, timestamp, source agent ID, and confidence metadata
- Staged items are JSONL files in `data/crowelm-unified/staging/pending/`
- After evaluation: moves to `staging/approved/`, `staging/review/`, or `staging/rejected/`

**Audit Logger** (`tools/audit_log.py`)
- Logs every agent action: tool calls, LLM requests, staging writes, evaluation decisions
- Structured JSONL logs in `data/crowelm-unified/logs/`
- Supports forensic reconstruction -- replay exactly what an agent did and why

**GitHub Actions Workflow** (`.github/workflows/crowelm-pipeline.yml`)
- `workflow_dispatch` trigger for production runs
- Docker isolation pattern from GitHub's agentic workflows architecture
- Commits approved examples back to the repo via PR (not direct push)

### New Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CROWELM_STAGING_DIR` | `data/crowelm-unified/staging` | Override staging location |
| `CROWELM_AUTO_APPROVE_THRESHOLD` | `0.85` | Score above this auto-approves |
| `CROWELM_REVIEW_THRESHOLD` | `0.5` | Score between this and auto-approve goes to human review |

### Directory Structure (new)

```
data/crowelm-unified/
  staging/
    pending/          # Awaiting evaluation
    approved/         # Passed gate, ready to merge
    review/           # Needs human review
    rejected/         # Below threshold, kept for analysis
  logs/               # Audit logs (structured JSONL)
  reports/            # Coverage reports, batch summaries
```

---

## Sub-Project 2: Evaluation Agents

### Coverage Analyzer (`agents/crowelm_coverage.yaml`)

- Scans the full dataset and produces a coverage report
- Maps examples per domain, per persona, per topic
- Identifies gaps: e.g., mycology has 2,222 examples but zero coverage on liquid culture, agar recipes, or monotub tek
- Outputs `reports/coverage_report.json` with prioritized gaps
- Runs on schedule (weekly via GitHub Actions) or on-demand

### Quality Scorer (`agents/crowelm_quality.yaml`)

Scores individual training examples on 4 dimensions:

| Dimension | What it checks |
|-----------|----------------|
| Accuracy | Is the instruction/response factually correct for the domain? |
| Specificity | Does the response contain actionable detail, not vague generalities? |
| Format consistency | Does it match CroweLM's expected instruction/response structure? |
| Deduplication | Semantic similarity against existing examples (flag if >0.92 cosine) |

Returns a 0-1 composite score per example. This score drives the tiered gate.

### Batch Auditor (`agents/crowelm_auditor.yaml`)

- Runs after a generation batch completes
- Cross-checks batch against coverage report -- did we fill the targeted gaps?
- Detects drift -- if generation agents produce off-topic or repetitive content
- Produces a batch summary for human review in `reports/`

### All evaluation agents:
- Run read-only against the dataset (no direct writes)
- Output structured JSON reports to `data/crowelm-unified/reports/`
- Use the audit logger for full traceability

---

## Sub-Project 3: Generation Agents

### Mycology Generator (`agents/crowelm_gen_mycology.yaml`)

Uses the `mycology_expert` persona (10+ years cultivation, <2% contamination rate, 1,500-2,000 blocks/month).

Reads the coverage report to target gaps. Generates instruction/response pairs across mycology subtopics:

- Substrate preparation (hardwood, straw, masters mix, supplementation)
- Contamination identification and prevention (trichoderma, cobweb, lipstick mold)
- Species-specific cultivation (shiitake, oyster, lion's mane, reishi, cordyceps)
- Fruiting conditions (FAE, humidity, temp, light cycles)
- Liquid culture, agar work, grain spawn, monotub tek
- Harvesting, drying, storage, yield optimization

**Target:** grow from 2,222 to 20,000+ mycology examples.
**Batch size:** 100 examples per run.

### Additional Generators (phase 2, after mycology hits 20K)

| Generator | Persona | Domain |
|-----------|---------|--------|
| `crowelm_gen_pharma.yaml` | pharma_researcher | Drug discovery, molecular biology |
| `crowelm_gen_bioprocess.yaml` | bioprocess_engineer | Fermentation, bioreactor design |
| `crowelm_gen_coder.yaml` | scientific_coder | Python bioinformatics, ML pipelines |

### Generation Flow Per Batch

1. Generator reads coverage report, picks highest-priority gap
2. Produces 100 examples targeting that gap
3. Writes to `staging/pending/` (never directly to curated)
4. Quality Scorer evaluates each example
5. Tiered gate: auto-approve / review queue / reject
6. Batch Auditor checks the batch holistically
7. Approved examples merge via PR (GitHub Actions) or direct append (local dev)

### Guardrails

- Generators have no internet access beyond the LLM proxy
- Output capped at 100 examples per run (prevents runaway generation)
- Content moderation strips URLs, PII, or off-domain content before staging
- All generation parameters (persona, target topic, model used) are logged

---

## Testing Strategy

### Infrastructure Tests (`tests/test_agent_runner.py`, `tests/test_staging_pipeline.py`, `tests/test_audit_log.py`)

- Agent runner launches container, confirms read-only dataset mount, confirms no env leak
- Staging pipeline correctly routes items through pending/approved/review/rejected
- Audit logger produces valid JSONL, captures all required fields
- Tiered gate applies thresholds correctly at boundary values (0.49, 0.5, 0.84, 0.85)

### Evaluation Agent Tests (`tests/test_crowelm_eval_agents.py`)

- Coverage analyzer produces valid report against a small fixture dataset
- Quality scorer returns 0-1 scores, handles edge cases (empty response, duplicate detection)
- Batch auditor detects drift when given intentionally off-topic examples

### Generation Agent Tests (`tests/test_crowelm_gen_agents.py`)

- Generator produces valid JSONL with required fields (instruction, response, category, persona)
- Output respects batch cap (never exceeds 100)
- Content moderation strips URLs and PII from generated content
- End-to-end: generate -> stage -> score -> gate -> approve, using mock LLM responses

### Contract Tests (`tests/contracts/`)

Human-maintained only. Agents cannot modify these. Inspired by Tyler McGoffin's approach at GitHub Copilot Applied Science.

- Staging directory structure is always preserved
- Approved examples always have a quality score attached
- Audit logs always have agent_id, timestamp, action fields
- Dataset JSONL schema is enforced (instruction, response required; category, persona optional)

---

## Build Order

| Phase | Sub-Project | Depends On | Ships |
|-------|-------------|------------|-------|
| 1 | Agent Infrastructure | Nothing | Secure runner, staging, logging, GitHub Actions workflow |
| 2 | Evaluation Agents | Infrastructure | Coverage, quality scoring, batch auditing |
| 3 | Generation Agents | Infrastructure + Evaluation | Mycology-first generation with tiered approval |

Each phase produces working, testable software independently.
