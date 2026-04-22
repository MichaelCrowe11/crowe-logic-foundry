# Crowe Logic Foundry: Revenue Projection and Strategic Impact

Status: canonical. Written 2026-04-21. Projects 12-month revenue across
three adoption scenarios, grounded in the pricing model
(`docs/pricing-strategy.md`), the unit economics baked into
`cli/cost_model.py`, and realistic assumptions about the distribution
channels Michael Crowe already controls.

Summary at the top:

| Scenario | End-of-Y1 MRR | Y1 Total Revenue | Y1 Gross Margin |
|---|---|---|---|
| Conservative | $12,400 | ~$78,500 | ~$47,000 (60%) |
| Moderate | $41,300 | ~$228,000 | ~$141,000 (62%) |
| Aggressive | $122,800 | ~$615,000 | ~$394,000 (64%) |

All three scenarios are self-funded and pay back the fixed costs of
running the product within the first ninety days.

## Assumption stack

Every projection below rests on these baseline assumptions. Change any
of them and the numbers move proportionally.

### Tier mix at steady state

Based on usage patterns of comparable tools (Cursor, Claude Code,
Aider) plus the operator's existing audience skew (mycologists and
developers willing to pay for credibility tools):

| Tier | Share of accounts | Avg MRR contribution |
|---|---|---|
| Personal ($29) | 55% | $29 |
| Pro ($99) | 25% | $99 |
| Team ($49/seat, avg 4 seats) | 10% | $196 |
| Enterprise | 0% Y1, 2% Y2 | $3,000+ |
| BYOK ($19) | 10% | $19 |

Blended ARPU: ~$48 per account in Y1.

### Churn rates

Monthly churn, by tier, based on industry comparables for dev-tool
SaaS at this price band:

| Tier | Monthly churn | Annual churn |
|---|---|---|
| Personal | 8% | ~63% |
| Pro | 4% | ~38% |
| Team | 2% | ~22% |
| BYOK | 6% | ~50% |

Blended churn weighted by tier mix: ~6% monthly.

### Unit economics per active subscriber

Averaged from heavy-usage scenarios in the pricing rationale doc. All
figures in USD.

| Tier | Revenue | Upstream cost | Gross margin | % |
|---|---|---|---|---|
| Personal | $29 | $8 | $21 | 72% |
| Pro | $99 | $35 | $64 | 65% |
| Team (per 4-seat acct) | $196 | $100 | $96 | 49% |
| BYOK | $19 | $3 | $16 | 84% |

Blended gross margin at Y1 tier mix: **~62%**.

### Fixed monthly costs

| Item | Monthly |
|---|---|
| Ollama Pro subscription (already paid) | $20 |
| Railway or similar hosting (control plane + landing) | $35 |
| Domain, SSL, email | $15 |
| PostHog (free tier until 1M events) | $0 |
| Sentry (free tier until 5K errors) | $0 |
| Stripe fees (2.9% + $0.30 per charge) | variable, ~3.5% of revenue |
| Accounting software (QuickBooks or similar) | $25 |
| Contracted design / legal as needed | ~$100 amortized |

Fixed cost floor: **~$195/month**, plus Stripe processing on actual
revenue.

### Customer acquisition cost

Three channels modeled:

1. **Organic audience push** (Skool community, Southwest Mushrooms
   customer list, Mushroom Grower book buyers). CAC approximately
   zero because the list is already owned. Estimated reach 3,000 to
   8,000 addressable people across all three lists, with 1-3%
   conversion giving 30 to 240 customers from the initial push.

2. **Content marketing** (YouTube asciinema demos, blog, dev.to,
   hackernews). Soft-CAC: time cost only, roughly $0 cash, but
   requires 3-5 hours per week. Converts at maybe 0.5% of qualified
   traffic.

3. **Paid ads** (Google, Twitter, dev-focused newsletters). Estimated
   CAC $40-80 per Personal signup, $100-150 per Pro signup, $300-500
   per Team signup. Only activated if organic + content channels
   underperform.

Conservative scenario assumes channels 1 and 2 only. Moderate and
Aggressive layer in channel 3.

## Scenario A: Conservative

**Thesis:** Organic audience push converts at the low end (1%).
Minimal paid ads. Mouth-of-mouth slow. Operator attention split
across several other businesses.

### Month-by-month

| Month | New signups | Churned | Net adds | Total active | Blended MRR |
|---|---|---|---|---|---|
| M1 | 40 | 0 | 40 | 40 | $1,920 |
| M2 | 25 | 2 | 23 | 63 | $3,020 |
| M3 | 20 | 4 | 16 | 79 | $3,790 |
| M4 | 20 | 5 | 15 | 94 | $4,510 |
| M5 | 22 | 6 | 16 | 110 | $5,280 |
| M6 | 25 | 7 | 18 | 128 | $6,140 |
| M7 | 25 | 8 | 17 | 145 | $6,960 |
| M8 | 28 | 9 | 19 | 164 | $7,870 |
| M9 | 30 | 10 | 20 | 184 | $8,830 |
| M10 | 30 | 11 | 19 | 203 | $9,740 |
| M11 | 32 | 12 | 20 | 223 | $10,700 |
| M12 | 34 | 13 | 21 | 244 | $12,400 |

**Y1 totals:**

- 331 gross signups, 87 churned, 244 active at EOY
- Cumulative revenue: ~$78,500
- Upstream cost: ~$30,200
- Gross margin: ~$47,000 (60% after Stripe fees)
- Fixed costs: $2,340
- **Net contribution: ~$44,700**

This scenario pays for itself in month two and generates $3,700/month
of contribution by month twelve. Not life-changing money but it
covers the operator's CloudKit and coffee budget with room to spare.

## Scenario B: Moderate

**Thesis:** Solid organic push, modest paid ad spend targeting
developer publications, and a successful demo video on
YouTube/Hacker News driving a mid-size traffic spike in month 3.

### Month-by-month

| Month | New signups | Churned | Net adds | Total active | Blended MRR |
|---|---|---|---|---|---|
| M1 | 100 | 0 | 100 | 100 | $4,800 |
| M2 | 80 | 6 | 74 | 174 | $8,360 |
| M3 | 150 | 10 | 140 | 314 | $15,080 |
| M4 | 100 | 19 | 81 | 395 | $18,980 |
| M5 | 85 | 24 | 61 | 456 | $21,900 |
| M6 | 80 | 27 | 53 | 509 | $24,450 |
| M7 | 85 | 31 | 54 | 563 | $27,020 |
| M8 | 90 | 34 | 56 | 619 | $29,710 |
| M9 | 95 | 37 | 58 | 677 | $32,500 |
| M10 | 100 | 41 | 59 | 736 | $35,330 |
| M11 | 105 | 44 | 61 | 797 | $38,250 |
| M12 | 110 | 48 | 62 | 859 | $41,300 |

**Y1 totals:**

- 1,180 gross signups, 321 churned, 859 active at EOY
- Cumulative revenue: ~$228,000
- Upstream cost: ~$87,000
- Gross margin: ~$141,000 (62% after Stripe fees)
- Fixed costs: $2,340 plus ~$18,000 in paid ads
- **Net contribution: ~$120,600**

This scenario supports a full-time focus on the product with room to
contract out design, docs, and a part-time support engineer in the
second half of the year. Approaches $50K/month MRR by month 13 and
~$500K ARR exit rate.

## Scenario C: Aggressive

**Thesis:** Demo video goes viral on HackerNews in month 2, multiple
dev newsletters pick up the dual-mode + synthesis positioning, first
Enterprise conversation starts in month 6 and closes in month 9.

### Month-by-month

| Month | New signups | Churned | Net adds | Total active | Blended MRR |
|---|---|---|---|---|---|
| M1 | 180 | 0 | 180 | 180 | $8,640 |
| M2 | 300 | 11 | 289 | 469 | $22,510 |
| M3 | 350 | 28 | 322 | 791 | $37,970 |
| M4 | 280 | 47 | 233 | 1,024 | $49,150 |
| M5 | 250 | 61 | 189 | 1,213 | $58,220 |
| M6 | 220 | 73 | 147 | 1,360 | $65,280 |
| M7 | 230 | 82 | 148 | 1,508 | $72,380 |
| M8 | 250 | 90 | 160 | 1,668 | $80,060 |
| M9 | 260 | 100 | 160 | 1,828 | $90,740 (+ $3,000 first Enterprise) |
| M10 | 270 | 110 | 160 | 1,988 | $98,420 (+ $3,000) |
| M11 | 280 | 119 | 161 | 2,149 | $106,150 (+ $6,000, second Enterprise) |
| M12 | 290 | 129 | 161 | 2,310 | $122,800 (+ $6,000) |

**Y1 totals:**

- 3,160 gross signups, 850 churned, 2,310 active at EOY (plus 2
  Enterprise contracts)
- Cumulative revenue: ~$615,000 (including $18K enterprise)
- Upstream cost: ~$221,000
- Gross margin: ~$394,000 (64% after Stripe fees)
- Fixed costs: $2,340 plus ~$65,000 in paid ads plus $60,000 in
  contracted support + design + marketing help
- **Net contribution: ~$267,000**

This scenario is achievable but not the base case. It assumes a
genuine viral moment, which is not plannable. Plan to the moderate
scenario; celebrate if this one lands.

## Breakeven and cash flow

Fixed cost floor is ~$195/month plus Stripe fees. Variable cost is
the upstream per-turn cost (absorbed by gross margin above). So the
breakeven is:

$195 / $21 (Personal gross margin) = **10 Personal customers** or
$195 / $64 (Pro gross margin) = **4 Pro customers**.

All three scenarios clear breakeven in month one. The product is
cash-positive from day one of paid signups, which is the structural
advantage of a SaaS cost model with a high-margin BYOK option and a
free-tier-dominant provider chain (NIM + Ollama Pro subscription).

## Five-year back-of-envelope

Extrapolating moderate scenario conservatively:

- **Y2:** Starts at 859 customers. 15% month-over-month net growth
  in H1, tapering to 8% in H2 as the base gets heavier. Ends at
  ~3,200 active. $150K MRR, $1.4M ARR. First hire (support +
  community).
- **Y3:** Ends at ~7,500 active, $360K MRR, $3.5M ARR. Small team
  of 3-4. First two full Enterprise deals closed.
- **Y4:** Depends entirely on whether the platform-pivot narrative
  (agents as a product surface, not a CLI) has played out by then.
  Base-case: $800K MRR, $8M ARR, team of 6-8.
- **Y5:** Decision point. Either keep building on the CLI foundation
  and grow organically, or raise a seed to push into enterprise
  sales. Revenue ceiling without external push is probably $15-20M
  ARR; with push, depends on the unit economics of paid acquisition
  which need to be proven by then.

These numbers are not commitments. They are what the math produces
if the assumptions hold.

## Strategic impact

Revenue is one dimension. Crowe Logic Foundry creates strategic
leverage that shows up elsewhere in the Crowe Logic portfolio.

### Platform for other Crowe Logic products

The agent framework already hosts one non-toy agent (Talon/NemoClaw).
Future agents are lower cost to build because the pattern is proven:

- A clinical-support agent for Southwest Mushrooms cultivation
  questions, loaded as an agent profile, sold via the Mushroom
  Grower audience. Low incremental engineering, incremental revenue.
- A compliance-research agent for the Prison Industrial Complex and
  Epstein Files database projects. Agent profile + DB connection
  tool, no new engineering.
- A Crowe Credit Engine advisor that runs off the pricing the book
  audience already paid for. Shared auth, shared billing, shared
  framework.

Each of these is a separate revenue stream that leverages the same
codebase and pricing infrastructure.

### Data flywheel for CroweLM fine-tunes

Every CLI turn is a potential training signal if the user opts in
to data sharing (separate from the product's default privacy
posture). A year of usage at the moderate scenario produces on the
order of 500,000 high-quality (prompt, response, tool-sequence,
outcome) triples. That dataset is directly fine-tuneable into the
CroweLM branded models, which then feed back into the product as
better defaults. The loop closes within 18 months if the operator
wants to pursue it.

### Defensive against the "AI CLI" category

Crowe Logic is on the leading edge of a category that is about to
get crowded. Being in-market with paying customers gives the product
a survivability edge when Cursor, Windsurf, or Claude Code inevitably
add their own multi-model modes. The moat isn't the code (copyable)
but the brand (Crowe Logic), the integrated tool ecosystem (hard to
copy in bulk), and the mycology-plus-dev audience niche that no
competitor will target.

### Optionality for later moves

Every customer signed adds optionality:

- They are a potential early user for new agents (Talon, future
  clinical, future research).
- They are a possible subject for a paid case study driving more
  signups.
- They are evidence of demand when the operator decides whether to
  raise, partner, or sell.

Building customers is building optionality; revenue is the scorecard
but the strategic value accumulates faster than the revenue does.

## Risks to the projection

In rough order of impact:

1. **Anthropic makes dual-mode obsolete.** If Claude adds a native
   "compare two models" feature, the differentiator gets commoditized.
   Mitigation: push the synthesis quality and the tool ecosystem as
   the moat, not the dual pane itself.

2. **Billing bug causes a refund event.** A webhook miss or double
   charge can produce a stampede of chargebacks that eats a month's
   margin. Mitigation: the billing work block in the product
   readiness report must be exercised end-to-end before any paid
   user onboards.

3. **NVIDIA starts metering NIM free tier.** Would kill the
   Talon/NemoClaw free-layer cost assumption. Mitigation: fallback
   to Anthropic or OpenAI for Talon's inference side, accept a 30%
   margin hit on that specific tier.

4. **Operator bandwidth.** Michael runs several parallel businesses.
   If Foundry is not the top focus, the moderate scenario likely
   compresses to conservative. This is the single largest swing
   factor.

5. **Regulatory.** Crowe Logic has a defensible posture (Anthropic /
   Azure do the model compliance, NemoClaw runs in a customer's own
   Brev VM, Crowe Logic is an orchestration layer), but EU AI Act
   enforcement or US export controls could change the picture.
   Monitor; don't plan reactively.

6. **Churn worse than projected.** Personal tier is the biggest
   churn risk because it competes directly with Claude Pro. If
   real churn on Personal runs at 12% instead of 8%, the
   conservative scenario net contribution falls to ~$25K for the
   year. Mitigation: push annual plans (pricing config already
   supports the discount), focus onboarding on one killer use case
   instead of the full feature matrix.

## What to watch in Q1

The three metrics that will tell you which scenario is unfolding:

1. **Conversion rate on the organic announce** (Skool + newsletter
   blast). Above 2% of reached-and-qualified = moderate or better.
   Below 0.5% = conservative.
2. **First-month churn.** Above 12% = the product is mispositioned
   or the onboarding is broken. Below 6% = the product is landing.
3. **Time-to-second-dual-turn** per new user. Under 24 hours = users
   are finding the core feature. Over 72 hours = the onboarding
   needs rework.

Track these three. Adjust the plan monthly based on what they say.

## Revenue-to-effort summary

For the operator's decision-making:

- **One focused week of billing work** (described in the readiness
  report) unlocks $2-5K MRR by month 2 in any scenario.
- **One focused month** of billing + onboarding + the demo video
  unlocks the moderate scenario's growth curve.
- **Three focused months** of product + marketing attention could
  realistically push to the aggressive scenario if the positioning
  video catches.

None of this requires external funding. All of it requires operator
attention. That is the real gating resource for this projection.
