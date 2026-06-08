# CroweLM Benchmark Scoreboard

## Track A — public benchmarks (backend baseline)

| Tier (backend) | Accuracy | N |
|---|---|---|
| DeepSeek-R1 | 100.0% | 2 |
| Kimi-K2-6 | 100.0% | 2 |
| gpt-5.4 | 100.0% | 2 |
| gpt-5.4-pro | 100.0% | 2 |

## Track B — mycology: grounded vs bare (the CroweLM delta)

| Tier (backend) | Grounded | Bare | Δ (delta) |
|---|---|---|---|
| gpt-5.4-pro | 4.00 | 3.00 | +1.00 |
| Kimi-K2-6 | 3.00 | 3.00 | +0.00 |
| gpt-5.4 | 3.00 | 4.00 | -1.00 |
| DeepSeek-R1 | 0.00 | 4.00 | -4.00 |

_Δ = grounded − bare on a 0–5 scale. The delta is the platform's contribution over the base model._