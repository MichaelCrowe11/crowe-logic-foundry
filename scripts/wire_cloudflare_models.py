#!/usr/bin/env python3
"""Wire the live Cloudflare Workers AI catalog into config/models.extra.json.

Idempotent: re-running only adds models not already present (dedups by
backend_name AND alias, so the alias resolver never gets ambiguous keys).

Cloudflare models are served through Foundry's existing `openai_compat`
provider against the Workers AI OpenAI-compatible endpoint
(CLOUDFLARE_AI_ENDPOINT) with the CLOUDFLARE_API_TOKEN key — the same pattern
the original 5 @cf failover tiers already use.

Only chat/inference-usable tasks are wired (Text Generation, Translation,
Image-to-Text → vision). Image-generation / speech / embeddings are NOT
chat-completions models and are intentionally excluded.

Usage:
    CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_API_TOKEN=... python3 scripts/wire_cloudflare_models.py
    # or, offline, from a previously-saved catalog:
    python3 scripts/wire_cloudflare_models.py --from-file /tmp/cf_usable.json
"""

import os, sys, json, re, urllib.request, urllib.parse, pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent
EXTRA = HERE / "config" / "models.extra.json"

# task -> Foundry tier "type"
TASK_TYPE = {
    "Text Generation": "reasoning",
    "Translation": "reasoning",
    "Image-to-Text": "vision",
}


def fetch_live():
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    tok = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not (acct and tok):
        sys.exit(
            "set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (or use --from-file)"
        )
    out = {}
    for task in TASK_TYPE:
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{acct}"
            f"/ai/models/search?task={urllib.parse.quote(task)}&per_page=100"
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        res = json.load(urllib.request.urlopen(req, timeout=30)).get("result", [])
        out[task] = [m["name"] for m in res]
    return out


def slug(model_id):
    s = model_id.replace("@cf/", "").lower()
    return "cf-" + re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def main():
    if "--from-file" in sys.argv:
        src = sys.argv[sys.argv.index("--from-file") + 1]
        raw = json.load(open(src))
        catalog = {
            t: [m["name"] if isinstance(m, dict) else m for m in v]
            for t, v in raw.items()
            if t in TASK_TYPE
        }
    else:
        catalog = fetch_live()

    doc = json.loads(EXTRA.read_text())
    # models.extra.json is {"models": [...]}; tolerate a bare list too.
    models = doc["models"] if isinstance(doc, dict) else doc

    have_backends = {m.get("backend_name") for m in models}
    have_keys = {m.get("name") for m in models}
    for m in models:
        have_keys.update(m.get("aliases", []))

    added = 0
    for task, ids in catalog.items():
        for mid in ids:
            if mid in have_backends:
                continue  # already wired (e.g. the original 5)
            name = slug(mid)
            if name in have_keys:
                continue
            base = mid.split("/")[-1]
            aliases = [a for a in (base,) if a and a not in have_keys]
            entry = {
                "name": name,
                "backend_name": mid,
                "label": f"Cloudflare: {base}",
                "aliases": aliases,
                "notes": f"Cloudflare Workers AI ({task}). Edge inference via the "
                f"Workers AI OpenAI-compatible endpoint.",
                "provider": "openai_compat",
                "type": TASK_TYPE[task],
                "endpoint_env": "CLOUDFLARE_AI_ENDPOINT",
                "api_key_env": "CLOUDFLARE_API_TOKEN",
            }
            models.append(entry)
            have_backends.add(mid)
            have_keys.add(name)
            have_keys.update(aliases)
            added += 1

    if isinstance(doc, dict):
        doc["models"] = models
        out = doc
    else:
        out = models
    EXTRA.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"added {added} Cloudflare models; new total = {len(models)}")


if __name__ == "__main__":
    main()
