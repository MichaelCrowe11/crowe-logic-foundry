# Crowe Logic Foundry Architecture

This repo is organized around a small set of runtime layers. The goal is to
keep provider logic, tool logic, host surfaces, and HTTP surfaces from
bleeding into each other as the product expands.

## Core layers

- `config`: environment loading, model registry, and static runtime config.
- `providers`: model backends and shared tool-calling loops.
- `tools`: callable capabilities and thin backend adapters used by providers and
  domain routes.
- `cli`: terminal and headless host surfaces that drive the runtime.
- `control_plane`: FastAPI composition root for HTTP routes and dashboard entry.
- `domain`: product-facing API routers that call tools.
- `knowledge`: knowledge-plane routers.
- `crowe_synapse_engine`: orchestration and pipeline core.
- `dashboard`: control-plane UI routes.
- `iterm`: iTerm-specific adapter code.

## Dependency directions

- `config` is a leaf. Nothing inside it should depend on other runtime layers.
- `crowe_synapse_engine`, `knowledge`, `dashboard`, and `iterm` are leaves.
- `domain` may depend on `tools`.
- `tools` may depend on `providers` for thin backend adapters.
- `providers` may depend on `config` and `tools`.
- `cli` may depend on `config`, `providers`, `tools`, `iterm`, and
  `crowe_synapse_engine`.
- `control_plane` is the HTTP composition root and may depend on `config`,
  `providers`, `domain`, `knowledge`, and `dashboard`.

## Temporary exception

There is one intentional exception today: some provider modules still import the
CLI `StreamRenderer` as a fallback when no renderer is passed in. That
dependency is currently allowed only in:

- `providers/_shared.py`
- `providers/azure_openai.py`
- `providers/anthropic.py`
- `providers/watsonx.py`

This exception is tracked by the architecture contract test so it cannot spread
to additional files unnoticed.

## Guardrail

`tests/test_architecture_boundaries.py` is the machine-checked contract for the
dependency rules above. If a new import crosses a forbidden boundary, that test
should fail before the coupling becomes part of the repo by accident.
