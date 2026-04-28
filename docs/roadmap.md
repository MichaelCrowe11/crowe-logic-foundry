# Crowe Logic Platform: Roadmap

Status: canonical. Refreshed 2026-04-27. Unified across all five
subsystems: Crowe Code (IDE), CroweLM (model), Research Engine (CLI/API),
Studio (content), control plane (auth + billing + metering).

Three lanes: **Now**, **Next**, **Later**. Two milestone anchors overlay
the lanes: **Closed Beta** and **Public Launch**.

For Studio-subsystem detail, see `STUDIO_ROADMAP.md`. For technical
readiness gates, see `docs/product-readiness.md`. For business and
launch detail, see `docs/blueprint.md` and `docs/launch-plan.md`.

---

## Milestones

| Milestone | Target | Entry criteria source |
|---|---|---|
| Closed Beta | **Achieved 2026-04-27** | Gate 1 in `product-readiness.md` |
| Public Launch | **2026-05-04 target** | Gate 2 in `product-readiness.md` |
| Scale | After Public Launch + 30 days of real traffic | Gate 3 in `product-readiness.md` |

---

## Now. This week

Items in flight. Cap of five so the lane reflects actual focus.

### Public Launch blockers
- [ ] Stripe webhook reconciliation in `control_plane/billing.py`. Path:
      receive event, look up subscription, update active-plan state,
      emit credit-balance delta. Test with replayed Stripe events.
- [ ] crowecode.com DNS wiring via `vscode/scripts/namecheap_dns_setup.py`.
      Vercel TXT verify record + A record at `76.76.21.21`.
- [ ] Public docs site at `docs.crowecode.com` (or subroute on the
      marketing site). Render existing `docs/` markdown.
- [ ] Cancel and refund flow documented + tested through the control plane.

### Crowe Code IDE polish
- [ ] Resolve the CFBundleIdentifier recovery so `Crowe Code.app` launches
      cleanly without `vscode/scripts/fix-broken-launch.sh`.
- [ ] Strip the em dash from the chat welcome copy in
      `deploy/ide/extensions/crowe-logic/src/views/chatView.ts:900`
      (style guideline violation).

---

## Next. Weeks 2 to 4

Items committed to but not yet in flight. Roughly three to six items at any time.

### Public Launch follow-up
- [ ] Status page at `status.crowecode.com` with provider availability.
- [ ] Public support channel (email + in-app form).
- [ ] Security disclosure policy + `SECURITY.md` in repo root.
- [ ] Terms of service + privacy policy.
- [ ] Trademark search for "Crowe Code" and "Crowe Studio" before any
      paid marketing spend.

### Provider coverage
- [ ] Usage publishing on secondary providers so the live HUD shows cost
      + credits across the full provider matrix, not just primary.
- [ ] Automatic failover policy: when primary provider returns a 5xx for
      N consecutive turns, fall back to secondary with a HUD notice.

### Studio (subsystem)
- [ ] Stronger relabel signal: re-run `relabel_per_frame.py` against
      `siglip-large-patch16-384` or `CLIP-ViT-H-14-laion2B`.
- [ ] CroweLM shot-selector: prompts CroweLM with script + per-camera
      metadata, returns an EDL.
- [ ] See `STUDIO_ROADMAP.md` for the full Studio backlog.

### CroweLM
- [ ] CroweLM Prime evaluation harness: hold-out set + per-tier
      comparison against the prior generation.
- [ ] Shot-selector fine-tune corpus from operator override telemetry.

### Crowe Knowledge (RAG)
- [ ] Tenant-scoped vector stores so each customer's RAG corpus is
      isolated.
- [ ] Embedding cache to avoid re-embedding unchanged documents on
      ingest.

---

## Later. Explicitly unscheduled

Capture without commitment. Items move from `Later` to `Next` when their
preconditions land or priority shifts.

### Platform
- [ ] Multi-tenant chat backend (Gate 3 entry criterion).
- [ ] On-call rotation + incident playbook (Gate 3).
- [ ] Aggregated observability dashboard for credit spend, provider
      errors, latency p95 (Gate 3).
- [ ] Disaster recovery: snapshot + restore tested under load (Gate 3).
- [ ] Capacity model with scaling triggers tied to active-user count.

### Crowe Code IDE
- [ ] Custom marketplace for first-party extensions, scoped to the
      Crowe Code fork.
- [ ] Native integrations with Talon Music Engine and Crowe Synapse
      Engine surfaces from inside the IDE.
- [ ] Inline cost preview before running an expensive turn.

### CroweLM
- [ ] CroweLM-vision multi-modal: 10-second clip in, structured scene
      analysis out (energy, subject count, aspect hint, platform fit).
- [ ] CroweLM-studio fine-tune trained on operator EDL overrides.

### Studio
- [ ] iOS app for multi-camera streaming (more than one iPhone, beyond
      Continuity Camera's single-iPhone limit).
- [ ] Cloud container fleet (`studio-cloud` on Fly.io) for compute-heavy
      vision models.
- [ ] OBS source plugin so Studio routes into existing OBS rigs.

### Research Engine
- [ ] Hosted API for external developers with usage-based billing.
- [ ] Citation export to Zotero, Notion, and the Research Engine native
      knowledge graph.

### Distribution
- [ ] First-party VS Code marketplace listing for the Crowe Code
      extension surface (separate from the IDE fork).
- [ ] Homebrew tap for `cl-agent` CLI.
- [ ] Direct B2B distribution to mycology and biotech labs through the
      operator's existing network.

---

## Shipped. Recent

Most recent first. Full history in `STUDIO_CHANGELOG.md` for Studio and
in `git log` for everything else.

### 2026-04-27
- RAG layer: `crowe-knowledge` vector store + populator + retrieval probe (PR #16).
- Path C launch: IDE chat webview, signup PAT, credit metering, themes (commit 46ba2f5).

### 2026-04-26
- Crowe Logic Workstation IDE rebrand on Apple Silicon (commit 1560bd1).

### 2026-04-22
- CroweLM Prime LoRA fine-tuning pipeline: curate + Azure FT (PR #14).

### 2026-04-21
- Hosted Crowe Research Engine endpoint, metered through credits (PR #9).
- IDE rebrand as Crowe Logic Workstation (PR #6).

### 2026-04-15 to 2026-04-20
- Migrations infrastructure (PRs #10, #11).
- Gateway model display rebrand (PR #12).
- Headless graceful provider skip (PR #7).

---

## How this roadmap stays current

`./scripts/weekly-readiness.sh` runs every Monday. It prints a checklist
for the operator: shipped commits since last update, items to move
between lanes, gate status changes, view drift. The operator confirms,
and this file is rewritten in place.

When an item ships, it moves to the `Shipped` section with the version
tag or PR link. When a milestone closes, the corresponding readiness
gate's verdict is updated in `product-readiness.md` and the milestone
date is fixed in this file's milestone table.
