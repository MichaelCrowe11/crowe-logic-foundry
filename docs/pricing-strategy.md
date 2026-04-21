# Crowe Logic Pricing Strategy

Status: canonical. Source of truth is `config/customer_pricing.json`. This
doc explains the reasoning so future edits stay coherent.

## Positioning wedge

Crowe Logic sells *multi-model orchestration* as a product. Every competitor
in the space falls into one of three buckets:

1. Single-model IDEs (Cursor, Windsurf, v0) charging $15 to $20 a month.
2. Single-vendor chat (Claude.ai, ChatGPT) at $20 a month with their own
   ecosystem.
3. BYOK CLIs (Aider, Cline, OpenCode) charging nothing and leaving token
   costs on the user.

None of them run two premium models concurrently against the same prompt
with a synthesis layer on top. None of them bundle a sandboxed shell
runtime (NemoClaw) with a multi-provider router. That is Crowe Logic's
moat. Price so that customers understand they are paying for the
orchestration, not just for a wrapper around one model.

## The four tiers

| Tier | Price | Credits | Who it's for |
|---|---|---|---|
| Personal | $29/mo ($25 annual) | 750/mo | Solo operators, mycologists, researchers |
| Pro | $99/mo ($85 annual) | 3,000/mo | Power users living in dual mode |
| Team | $49/seat/mo ($42 annual, 3+ seats) | 1,500/seat pooled | Small shops, content teams, labs |
| Enterprise | custom ($250 floor) | unlimited | Orgs with compliance, audit, dedicated infra needs |

Plus a **BYOK variant** at $19/mo: you bring provider API keys, we provide
orchestration, dual mode, synthesis, tools, HUD. No credit meter.

### Why $29 for Personal

It signals premium without pricing out the individual persona. Claude Pro,
ChatGPT Plus, Cursor Pro all land at $20. Crowe Logic at $29 says "you are
paying 45% more because you get to run two flagship models against one
prompt and get an AI-synthesized answer, plus a sandboxed shell, plus the
full multi-provider chain". If customers feel that is not worth the
premium, they were never the right customer for this tier.

### Why $99 for Pro, not $79

Two reasons. First, heavy dual-mode users with synthesis enabled consume
10 to 15 credits per turn. At $79 with the same 3,000-credit allocation,
margin on the top 10% of users thins below 50%. Second, Claude.ai Max 5x
is at $100 and Cursor Pro+ is at $60. Pricing in that mid-hundred band
anchors Crowe Logic as a peer tool, not a budget option. Annual at $85
is the courtesy discount to lock revenue.

### Why $49 for Team, not $40

Windsurf Teams is $30/seat, Cursor Teams is $40/seat, Claude Team Premium
is $100/seat with Claude Code. Crowe Logic Team at $49 sits above Cursor's
undercut-of-Cursor positioning (that is Windsurf's game), below Claude
Team Premium, and justified by the shared workspace and admin cost
reporting features teams actually care about. Margin on Team is
deliberately lower (around 49% at heavy usage) because team deals trade
margin for ACV and stickiness.

### Why the BYOK tier exists

A meaningful slice of potential customers already have Anthropic / OpenAI /
Moonshot accounts with their own billing set up. Forcing them to re-route
through Crowe Logic's credit system means they pay twice. The $19 BYOK
tier captures them without cannibalizing Personal or Pro: the UX is
identical, but the credit meter is off and Crowe Logic never touches their
provider bills. Margin is nearly pure (infrastructure + minor NIM/Ollama
amortization).

## The credit system

Credits decouple customer-facing pricing from vendor rate cards. Why this
matters: Anthropic dropped Opus from $15/$75 to $5/$25 per MTok between
September 2025 and April 2026. A fixed-dollar per-token passthrough would
have forced a customer-facing price change. Credits insulate the customer
from that volatility.

| Action | Credits |
|---|---|
| Turn on a fast model (Haiku, CroweLM Nano) | 1 |
| Turn on a balanced model (Sonnet, K2.5, Granite) | 2 |
| Turn on a flagship model (Opus 4.7, K2.6, GPT-5.4) | 5 |
| Dual mode turn | sum of both sides (usually 10) |
| Synthesis turn added | +5 (one flagship turn) |
| First 10 tool calls per turn | free |
| Tool call overage | 1 credit per 10 |
| Browser automation session | 3 |
| NemoClaw sandbox turn | +2 |

A Pro user running dual mode with synthesis on every turn can do about 200
such turns per month on the 3,000-credit allocation. Without synthesis,
about 300. Light dual-mode users get 500-plus.

### The intentional flagship subsidy

Opus costs about 15x more than Haiku upstream. Crowe Logic's credit ratio
is 5x (5 credits for flagship, 1 for fast). That 3x subsidy on flagship
is intentional. It pushes customers to try the premium models, which is
where Crowe Logic's differentiation lives. A customer who only ever runs
Haiku has no reason to stay at Crowe Logic over Aider. A customer who
ran Opus twice and saw the synthesis land is hooked.

## Unit economics

Heavy usage assumptions: 500 user turns per month, 40% flagship, 40%
balanced, 20% fast. Average input 20K tokens, output 2K, 70% cache hit
rate on Anthropic after the second turn of a session.

| Tier | Revenue | Upstream cost | Gross margin | % |
|---|---|---|---|---|
| Personal ($29) | $29.00 | ~$8 | $21 | 72% |
| Pro ($99) | $99.00 | ~$35 | $64 | 65% |
| Team ($49/seat × 3) | $147.00 | ~$75 | $72 | 49% |
| Enterprise ($250 floor) | $250.00+ | ~$80 | $170 | 68% |
| BYOK ($19) | $19.00 | ~$3 (infra) | $16 | 84% |

Averaged at a 40/30/20/5/5 mix of tiers, blended gross margin lands
around 62%. Comparable SaaS platforms at this stage operate 60-80%
gross on subscription, so 62% is defensible for a first-year price
card with room to optimize upward as Anthropic caching and NVIDIA
free-tier coverage expand.

### Subscription-covered models

Ollama Pro at $20/mo covers every `:cloud` tagged model we route
through Ollama. The cost model amortizes that $20 across the
customer's Ollama-cloud turns in the month. A customer doing 50
Eclipse turns per month sees $0.40 per turn of attributed
subscription cost. A customer doing 500 Eclipse turns sees $0.04.
This is why Pro customers are unit-economically better than Personal
even at lower margin percent: they saturate the fixed subscription.

## Longevity and defensibility

Three forces will pressure this price card over the next 12 months.

1. **Model prices continue to drop.** Opus fell 3x in six months; Sonnet
   and Haiku will follow. The credit system absorbs this. Every price
   drop increases Crowe Logic's margin without any action on our part.

2. **Competitors bundle multi-model.** Cursor already lets users switch
   between models; eventually someone else copies the concurrent dual
   pane. The moat here is the synthesis quality and the tool ecosystem
   (NemoClaw sandbox, MCP breadth, iTerm2 control, domain agents).
   Invest those deepening-moat features ahead of the competitive
   squeeze, not after.

3. **BYOK erosion.** The $19 BYOK tier is honest. Power developers with
   existing accounts will pick it. Assume 20-30% of acquisitions route
   here. That is fine: BYOK customers still convert to Pro or Team when
   they want the credit convenience, and BYOK has 84% margin as-is.

## When to revisit

- **Every six months** for vendor rate-card changes (update
  `upstream_costs.json`, rerun margin_report, confirm tiers still
  healthy).
- **Immediately** if a top competitor launches a concurrent dual-model
  feature within 10% of our pricing.
- **At 10,000 paying customers** for the first tier restructure.
  Likely: introduce a $199 "Pro Plus" with 10K credits for team leads
  who don't need full Team admin but run very heavy loads.

## What this file is not

This file is not operational. The numbers customers see come from
`config/customer_pricing.json`. The cost numbers in the HUD come from
`config/upstream_costs.json`. This doc explains why those two files
look the way they do so future maintainers do not unwind the
reasoning by accident.
