# Crowe Logic Platform: Launch Plan

Status: canonical. Written 2026-04-27. Plan for Public Launch and the
first ninety days. Detail behind the launch section of
`docs/blueprint.md`.

## Public Launch target

**2026-05-04 (Monday).** Realistic if the Gate 2 blockers in
`product-readiness.md` clear during the week of 2026-04-27. The launch
will slip a week per blocker that surprises us. The webhook
reconciliation work is the single most likely cause of slip.

## What "launch" means here

Three concrete deliverables in launch week:

1. `crowecode.com` resolves to the marketing site with a working signup
   path that issues a free PAT in real time.
2. The operator publicly posts about the launch on at least three
   channels (X, Skool community, Show HN).
3. The first non-friend customer converts to a paid tier within seven
   days. If conversion takes longer, the launch is treated as soft and
   the conversion gates are revisited.

## Pre-launch checklist

In execution order. Tracked in `docs/roadmap.md` under `Now`.

### Technical
- [ ] Stripe webhook reconciliation lands and is tested with replayed
      events.
- [ ] `crowecode.com` DNS configured (Vercel TXT verify, A record).
- [ ] Marketing site deployed at `crowecode.com` with signup CTA.
- [ ] Cancel and refund flow tested end-to-end (signup, subscribe,
      cancel within 24 hours, refund issued).
- [ ] Public-tier metering enforcement verified under load (burst test
      that spikes 100 turns in 60 seconds).
- [ ] Public docs site at `docs.crowecode.com` rendering existing
      `docs/` markdown.
- [ ] Status page at `status.crowecode.com`.

### Operational
- [ ] Public support channel (`support@crowecode.com` mailbox monitored).
- [ ] `SECURITY.md` in repo root with disclosure policy and PGP key.
- [ ] Terms of service and privacy policy live on the marketing site.
- [ ] Trademark search complete for "Crowe Code" and "Crowe Studio".
      Outcome may shift naming before launch.

### Content
- [ ] Launch blog post draft.
- [ ] Two short demo videos: dual-mode synthesis, and Research Engine
      pipeline.
- [ ] Show HN headline draft (one-line that names the wedge).
- [ ] Three follow-up tweets queued for launch day.

## Launch day sequence

Times in operator-local (Phoenix, MST).

| Time | Action |
|---|---|
| 06:00 | Final smoke test through signup, dual-mode, synthesis, cancel. |
| 08:00 | Marketing site live. Status page live. |
| 09:00 | Operator posts on personal X. |
| 10:00 | Skool community announcement. |
| 11:00 | Show HN submission. |
| 12:00 to 18:00 | Operator monitors `#support` and Show HN comments. |
| 18:00 | First daily debrief: signups, conversions, support tickets. |

If the Show HN post lands a top-five slot, the operator suspends other
work and stays with the comments thread for the duration. If the post
lands below top twenty within an hour, the launch is treated as soft
and the operator pivots to direct outreach in target communities.

## First seven days after launch

| Day | Focus |
|---|---|
| 1 | Monitor signup funnel. Patch any conversion-blocking bug. |
| 2 | First customer interview if any signed up. Capture three direct quotes. |
| 3 | Publish customer interview as a blog post if the quotes are usable. |
| 4 | Outreach to four target AI tooling newsletters with a one-paragraph pitch. |
| 5 | Second customer interview. Look for tier-mix signal. |
| 6 | Public retrospective post: signups, conversions, what surprised us. |
| 7 | Internal review: tier mix actual vs assumed in `revenue-projection.md`. |

## First thirty days after launch

Three numbers tracked weekly:

1. **Trial-to-paid conversion rate.** Target: 20% of signups upgrade
   within 14 days. Lower than 10% means the synthesis pane is not
   landing as a conviction moment.
2. **Tier mix.** Target: matches the assumption in
   `revenue-projection.md` within 10 percentage points per tier.
3. **Churn at day 30.** Target: under 5% monthly. Higher means the
   activation flow is not creating habit.

If any number trends 30% off target for two consecutive weeks, we
treat the launch as needing a positioning rethink rather than a feature
addition.

## First ninety days

Two strategic decisions land here:

1. **Whether to start paid acquisition.** Gated on conversion rate and
   churn meeting the 30-day targets above. If both green, allocate up
   to $2K/mo to one channel (likely Twitter ads or sponsorship in one
   AI tooling newsletter). If either red, no paid spend.
2. **Whether to begin the Scale gate work.** Gated on aggregate active
   users crossing 100. Below that, multi-tenant chat backend is
   premature. Above that, it is launch-blocking for the next 90 days.

## Launch positioning

The wedge sentence: **"Crowe Code is the IDE that runs two flagship
models against your prompt and synthesizes the answer."**

Variants for different channels:

- For developers (Show HN, X): "Multi-model AI IDE. Two models,
  concurrent, synthesized output. Plus a research engine and a content
  pipeline that share the same credit balance."
- For mycologists and researchers: "Crowe Logic. The platform that
  turns your domain expertise into AI-native research workflows."
- For content teams: "Crowe Studio. Multi-camera capture, automated
  shot selection, all powered by the same AI infrastructure that
  powers Crowe Code."

All three avoid naming providers. Name-drop only happens in technical
documentation and the developer-targeted Show HN comments.

## What we are not doing in launch week

- No paid ads.
- No founder podcasts or media interviews until Day 14 or later. The
  product needs to absorb its own launch first.
- No new features. Feature freeze starts 2026-05-01 and lifts after
  Day 14.
- No price changes. The current tier table is locked through Day 90.

## What the operator commits to

Solo operator launch reality:

- Three-day launch sprint (Sat 2026-05-02 through Mon 2026-05-04).
  Other commitments cleared.
- Two-week feature freeze post-launch. Bug fixes and conversion
  patches only.
- Daily customer-conversation slot, 1 hour per day for the first 30
  days. This is the slot that catches positioning failures early.
- Weekly readiness update via `./scripts/weekly-readiness.sh` so the
  numbers do not drift.

## Post-90-day next horizon

Decided at the Day 90 mark, not now. Likely options in priority order:

1. Launch the Crowe Code marketplace for first-party extensions.
2. Open the Research Engine API for external developers.
3. Begin Scale gate work driven by traffic.
4. Launch Studio as a standalone tier for content creators.

The Day 90 decision is data-driven against the three numbers above plus
qualitative signal from customer conversations.
