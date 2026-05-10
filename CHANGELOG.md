# Changelog

All notable changes to the Crowe Logic Foundry are recorded here. Format follows
Keep a Changelog (https://keepachangelog.com/en/1.1.0/) and the project follows
semantic-ish versioning aligned with `pyproject.toml`.

## [Unreleased]

### Added
- CroweLM Sage tier backed by `deepseek-v4-flash:cloud` on Ollama Cloud. 1M-token
  context, 284B total / 13B active MoE, US-hosted. Aliases: `sage`, `crowelm-sage`,
  `v4-flash`, `deepseek-v4-flash`, `crowelm-v4-flash`.
- CroweLM Loom tier backed by `deepseek-v4-pro:cloud` on Ollama Cloud. Deepest
  reasoning tier currently available through Ollama's hosted catalog,
  1.6T total / 49B active MoE. Aliases: `loom`, `crowelm-loom`, `v4-pro`,
  `deepseek-v4-pro`, `crowelm-v4-pro`.
- CroweLM Sonnet tier on the direct Anthropic API (`api.anthropic.com`),
  independent of Azure-Anthropic resources. Backed by `claude-sonnet-4-6`.
  Aliases: `sonnet`, `crowelm-sonnet`, `claude-sonnet`.
- `scripts/smoke_v4_cloud_swap.py` covering registry merge, alias resolution,
  Anthropic provider routing for direct vs Azure endpoints, and live Ollama
  Cloud reachability for both V4 tiers (22 checks).
- `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` environment variables. Leaving
  `ANTHROPIC_BASE_URL` empty triggers the Anthropic SDK default
  (`https://api.anthropic.com`); set it only for regional gateways or proxies.

### Changed
- `providers/anthropic.py` now honors empty or `api.anthropic.com` endpoints by
  delegating to the Anthropic SDK default base URL. The Azure-Anthropic flow is
  unchanged: any other non-empty endpoint still has `/anthropic` appended when
  it isn't already present.
- `.env.example` documents the parallel-run posture: existing `AZURE_*` LLM
  variables remain valid and active; the new Ollama Cloud and direct Anthropic
  paths are additive landing zones rather than hard cutovers.

### Notes
- This release is the LLM-tier landing zone for the Azure scrap. Existing
  Azure-backed tiers (Supreme, Sovereign Premium, Prime Premium, Titan Premium,
  Apex Premium, Dense Managed, etc.) are deliberately untouched. Flip
  individual tiers off Azure when ready by adding override entries to
  `config/models.extra.json` whose `name` matches the existing tier.
- Sora video generation (`AZURE_SORA_*`) and the Azure ML GLM-5.1 deployment
  (`AZURE_GLM51_*`) remain on Azure and are explicitly out of scope. Replacing
  Sora requires migrating to a different video pipeline (Veo, Kling, Runway);
  replacing GLM-5.1 requires picking a new host (Together, Fireworks, or
  self-managed vLLM).
