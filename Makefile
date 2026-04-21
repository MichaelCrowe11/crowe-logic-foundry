# Crowe Logic Foundry — developer Makefile
PY := .venv/bin/python
export PYTHONPATH := $(CURDIR)

.PHONY: help venv install lint fmt test preview prod chat key admin e2e clean

help:
	@echo "Crowe Logic Foundry — make targets"
	@echo "  make install   # pip install -e . + dev deps into .venv"
	@echo "  make lint      # ruff check"
	@echo "  make fmt       # ruff format"
	@echo "  make test      # pytest -q"
	@echo "  make preview   # run control-plane on :8001 (SQLite)"
	@echo "  make prod      # uvicorn reload on :8001 (real DB)"
	@echo "  make chat      # interactive Crowe Logic CLI"
	@echo "  make key L=alex-dev P=lab    # issue local tester key"
	@echo "  make key-remote L=alex-dev   # issue via live control plane"
	@echo "  make admin                    # bootstrap admin JWT to .env.local"
	@echo "  make e2e                      # smoke: preview + key + gateway call"
	@echo "  make clean                    # remove caches + preview db"

venv:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip

install: venv
	$(PY) -m pip install -e .
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install ruff pytest pytest-asyncio bcrypt asyncpg fastapi uvicorn httpx

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

test:
	$(PY) -m pytest -q

preview:
	$(PY) control_plane/preview.py

prod:
	$(PY) -m uvicorn control_plane.main:app --host 0.0.0.0 --port 8001 --reload

chat:
	$(PY) -m cli.crowe_logic chat

L ?= tester-dev
P ?= lab
key:
	$(PY) scripts/issue_tester_key.py --label $(L) --plan $(P)

key-remote:
	$(PY) scripts/issue_tester_key.py --remote --label $(L) --plan $(P)

admin:
	$(PY) scripts/bootstrap_admin.py --save

e2e:
	bash scripts/tester_e2e.sh

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
	rm -f data/control_plane_preview.sqlite
