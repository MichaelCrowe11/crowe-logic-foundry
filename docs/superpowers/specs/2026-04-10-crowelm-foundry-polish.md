# CroweLM Foundry Polish

**Date:** 2026-04-10  
**Scope:** Final polish pass on the CroweLM Azure AI Foundry stack, CLI rendering path, package surface, and design direction.

## Implemented in this pass

- `CroweLM Pro` is now wired to surface reasoning summaries in both the Rich terminal renderer and the JSON headless stream when Azure returns reasoning summary events.
- The Azure Responses provider uses streamed deltas first, then falls back to finalized response content if Azure returns reasoning or answer text only at completion time.
- Tool-calling behavior remains intact on the Responses path, with the same local function execution loop used before this pass.
- Unit coverage now exists for:
  - streamed reasoning + text
  - tool-call round-trips on the Responses API
  - finalized-response fallback behavior

## Creative Direction

The CroweLM stack should feel less like a generic model router and more like an authored instrument.

- Visual tone: scientific gold on dark mineral surfaces, precise rather than ornamental.
- Interaction tone: sparse, deliberate, high-signal. The CLI should feel like a calibrated console, not a chatbot toy.
- Motion tone: reasoning appears as a visible prelude, not hidden machinery. The model should seem to gather force before it speaks.
- Naming tone: `CroweLM Pro`, `CroweLM Opus`, `CroweLM Core`, `CroweLM Kernel`, and `CroweLM Motion` form one family. The user should never see raw vendor names unless they are debugging infrastructure.

The design target is "ritual precision":

- every label should read as intentional
- every waiting state should feel alive
- every model tier should suggest a distinct capability, not just a different endpoint

## Dependency and Package Audit

### Python runtime

Current core runtime is coherent:

- `openai` powers the Azure OpenAI-compatible and Responses surfaces
- `anthropic` powers Azure-hosted Claude
- `rich`, `prompt-toolkit`, and `click` form the CLI shell
- `httpx`, `beautifulsoup4`, and `PyYAML` support tools and registries

Observed package risk:

- `openai>=1.0.0` is too broad for the current implementation. The Responses streaming path now depends on a modern SDK surface, so this should be pinned to a newer minimum in a follow-up hardening pass.
- `azure-ai-agents`, `azure-ai-projects`, and `azure-identity` are still required by the legacy deploy and script paths, but they are no longer part of the primary CroweLM inference path. They should eventually move behind an optional extra or legacy install target.

### Node package surface

The npm package is intentionally thin:

- `npm/bin.js` delegates to the Python CLI
- MCP- and Playwright-related dependencies support surrounding workflows, not the wrapper itself

Optimization path:

- keep the npm wrapper minimal
- separate optional MCP server bundles from the base wrapper if install size becomes a concern
- avoid turning the npm package into a second implementation of the CLI

## Strategic Path

### Phase 1: Stability and trust

This phase is now largely complete.

- first-party CroweLM model branding
- live Azure routing for Pro, Opus, Core, Kernel, GLM, and Motion
- reasoning-ready visibility in terminal and headless hosts
- installable `crowe-logic` command validated against live endpoints

Current live limitation:

- The provider now requests reasoning summaries explicitly, but Azure did not emit reasoning summary tokens in the final smoke prompts used during this pass. The renderer path is ready; provider-side availability remains model- and prompt-dependent.

### Phase 2: Packaging hardening

Next highest-leverage work:

- pin modern minimum versions for `openai` and `anthropic`
- split legacy Azure Agents dependencies into optional extras
- add a fast smoke suite that can be run against live deployments from CI or release workflows
- add lockfile guidance for both Python and Node entrypoints

### Phase 3: Host unification

Make every shell around CroweLM feel like the same product:

- align IDE/chat-extension labels to the CroweLM naming family
- surface reasoning consistently anywhere the headless stream is consumed
- standardize error text, health checks, and model selection semantics across terminal, IDE, and MCP-adjacent hosts

### Phase 4: Prompt and tuning system

Fine-tuned prompts should become a first-class asset, not scattered literals.

- create a prompt registry per CroweLM tier
- version prompt packs independently from provider code
- attach benchmark tasks to each tier so prompt edits are measurable
- treat fine-tune datasets, evals, and prompt variants as release artifacts

## End-to-End Implementation Principle

The strongest path is not "more models." It is tighter product coherence:

- one naming language
- one reasoning surface
- one deploy story
- one release discipline

That is how CroweLM stops looking like a routed collection of vendor endpoints and starts reading as a single system.
