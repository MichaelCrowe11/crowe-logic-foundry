# Product Readiness, Roadmap, and Blueprint: Living Documents

Status: design spec. Written 2026-04-27. Defines the structure, scope, and
working cadence for a continuously-iterated set of artifacts that describe
the Crowe Logic platform's readiness, plan, and architecture.

## Goal

Establish three living documents (readiness, roadmap, blueprint) that stay
current week over week and serve as the canonical source of truth for
internal execution, investor conversations, and customer-facing positioning.

## Decisions

Captured from the brainstorming dialogue on 2026-04-27.

| Question | Decision |
|---|---|
| Scope | Crowe Logic Platform (unified). Crowe Code, CroweLM, Research Engine, Studio, control plane treated as one integrated product surface. |
| Audience | Hybrid. Single internal master doc as source of truth; sanitized investor view and customer view derived from it. |
| Time horizon | Now / Next / Later execution lanes with two named milestone anchors: Closed Beta and Public Launch. |
| Readiness criteria | Multi-tier: Closed Beta, Public Launch, Scale, each with explicit entry criteria. |
| Blueprint scope | Architecture + business + launch. (Org design deferred until past solo-operator stage.) |
| Cadence | Weekly check-in. State review, surface shipped/slipped, propose updates, operator approves. |

## Document set

```
docs/
  product-readiness.md     refreshed: today's date, multi-tier criteria
  roadmap.md               new: unified Now/Next/Later for whole platform
  blueprint.md             new: integrative narrative + diagrams
  launch-plan.md           new: GTM, channels, positioning
  ARCHITECTURE.md          existing: technical layer contract
  revenue-projection.md    existing: financial model
  pricing-strategy.md      existing: tier pricing rationale
  developer-report.md      existing: engineering state
  views/
    investor.md            new: sanitized investor view
    customer.md            new: customer-facing positioning (no tech stack exposure)
scripts/
  weekly-readiness.sh      new: prints weekly review checklist
```

The internal master is the union of `product-readiness.md`, `roadmap.md`,
`blueprint.md`, `launch-plan.md`, plus the existing detailed reports.
The two derived views in `docs/views/` are written by hand at first; if
they drift more than a week behind master, automate them later.

## Readiness gates

Three explicit gates the readiness report tracks separately. Each gate has
binary entry criteria. The report says where the platform sits on each gate.

### Gate 1: Closed Beta
Used by under twenty operators, with manual onboarding allowed. Subscription
enforcement on the CLI, end-to-end provider coverage, and a working
control-plane signup path. Status as of 2026-04-27: **achieved**.

### Gate 2: Public Launch
Self-serve subscribe and downgrade. Stripe webhook reconciliation. Public
crowecode.com landing surface live. Documented refund and cancellation flow.
Public-tier metering enforced. Status as of 2026-04-27: **one blocker
remaining** (billing webhook reconciliation).

### Gate 3: Scale
Multi-tenant routing on the IDE chat backend, on-call rotation defined,
incident playbook drafted, observability covering credit spend and provider
errors, support SLO published. Status as of 2026-04-27: **not started**.

## Roadmap shape

Three lanes plus two milestone anchors.

- **Now**: in flight this week. Item count capped at five so the lane
  reflects actual focus.
- **Next**: weeks two through four. Roughly three to six items.
- **Later**: explicitly unscheduled. Captures ideas without committing.

Two milestone anchors overlay the lanes:

- **Closed Beta** target date and entry criteria.
- **Public Launch** target date and entry criteria.

When an item ships, it moves to a `Shipped` section with the version tag
and changelog link. When a milestone is hit, the readiness gate is closed
and the report's verdict is updated.

## Blueprint shape

Three sections. Each section is one page or shorter. Detail lives in linked
deep docs.

- **Architecture**. One diagram showing the five subsystems (Crowe Code IDE,
  CroweLM, Research Engine, Studio, control plane), data flow, and deploy
  topology. Explains how the credit ledger ties the surfaces together.
- **Business**. Tier table, blended ARPU, margin per tier, distribution
  channels, the three Y1 revenue scenarios. References
  `revenue-projection.md` and `pricing-strategy.md` for detail.
- **Launch**. The GTM sequence: who the launch is for, where they hear
  about it, what they do in the first ten minutes, how they convert.
  References `launch-plan.md` for detail.

## Derived views

### Investor view
Reads as a one-page summary plus the blueprint. Removes engineering
specifics (provider names, model IDs, internal blockers under thirty
days of effort) but keeps the readiness gate verdict, the financial
model, and the launch sequence.

### Customer view
Reads as positioning, not strategy. No tech stack exposure (no
provider names, no internal architecture). Leads with what the
customer can do today, ends with how they get started. Lives at
`docs/views/customer.md` until the marketing site renders it.

## Weekly cadence

Every Monday during the working week the operator runs:

```
./scripts/weekly-readiness.sh
```

Which prints a one-page checklist:
- Diff since last update: shipped commits, opened/closed issues, demo wins.
- Items to move on the roadmap (Now -> Shipped, Next -> Now, Later -> Next).
- Readiness gate re-check: any blocker cleared or surfaced?
- Investor / customer view drift check.

The operator confirms each line, the assistant rewrites the affected
files, runs the spec self-review, and commits.

## Non-goals

- Not building a project-management UI. The docs are flat markdown.
- Not generating a marketing site. The customer view is markdown that
  the marketing site can later render.
- Not introducing org chart, hiring plan, or contractor management.
  Those wait until the platform supports more than one operator.

## Open questions

None blocking. The first weekly review will surface anything we missed.
