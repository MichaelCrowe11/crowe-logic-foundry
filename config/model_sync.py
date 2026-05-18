"""Generate loadable CroweLM model entries from Azure deployment inventory."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from config.crowelm.rebrand_map import display_label, is_leaky_label


DEFAULT_MODELS_PATH = Path.home() / ".config" / "crowe-logic" / "models.extra.json"
LEGACY_MODELS_PATH = Path.home() / ".crowe-logic" / "models.extra.json"
LABEL_ALIASES = {
    "gpt": "GPT",
    "glm": "GLM",
    "k2": "K2",
    "nim": "NIM",
    "oss": "OSS",
}


def deployment_name(deployment: dict) -> str:
    """Return the deployment name from a CLI/API payload."""
    for key in ("name", "deploymentName", "deployment_name"):
        value = str(deployment.get(key, "")).strip()
        if value:
            return value
    raise ValueError(f"Could not determine deployment name from: {deployment!r}")


def infer_provider(name: str) -> str:
    """Infer the provider kind from a deployment name."""
    return "anthropic" if "claude" in name.lower() else "azure_openai"


def infer_surface(name: str) -> str | None:
    """Infer the Azure OpenAI surface from a deployment name."""
    lname = name.lower()
    if lname.startswith(("gpt-5", "o1", "o3", "o4")) or "gpt-5" in lname:
        return "responses"
    return None


def default_envs(provider: str) -> tuple[str, str]:
    """Return the endpoint/api-key env vars for a provider."""
    if provider == "anthropic":
        return ("AZURE_ANTHROPIC_ENDPOINT", "AZURE_ANTHROPIC_API_KEY")
    return ("AZURE_CORE_ENDPOINT", "AZURE_CORE_API_KEY")


def label_for(name: str) -> str:
    """Build a human-readable CroweLM label from a deployment name."""
    words = name.replace("_", "-").split("-")
    rendered = [
        LABEL_ALIASES.get(word.lower(), word.upper() if word.isupper() else word.capitalize())
        for word in words
        if word
    ]
    return " ".join(rendered)


def build_extra_model_entry(deployment: dict) -> dict:
    """Convert one deployment payload into an extra-model entry.

    Display label resolution:
      1. Consult REBRAND_MAP (config.crowelm.rebrand_map) for an explicit
         Crowe Logic codename. This is the no-leak path.
      2. Fall back to the mechanical builder ("CroweLM <Title Case Name>").
      3. If we used the rebranded codename, push the mechanical label into
         `aliases` so legacy resolvers still match.
      4. If the final label is leaky, emit a one-line warning to stderr so
         operators can add the deployment to REBRAND_MAP.
    """
    name = deployment_name(deployment)
    provider = infer_provider(name)
    endpoint_env, api_key_env = default_envs(provider)
    mechanical = f"CroweLM {label_for(name)}"
    label = display_label(name, fallback=mechanical)
    aliases: list[str] = []
    if label != mechanical:
        aliases.append(mechanical)
    if is_leaky_label(label):
        print(
            f"warning: leaky label for deployment {name!r}: {label!r}. "
            "Add an entry to config/crowelm/rebrand_map.py:REBRAND_MAP.",
            file=sys.stderr,
        )
    entry = {
        "name": name,
        "label": label,
        "provider": provider,
        "type": "reasoning",
        "endpoint_env": endpoint_env,
        "api_key_env": api_key_env,
        "aliases": aliases,
    }
    surface = infer_surface(name)
    if surface:
        entry["surface"] = surface
    return entry


def build_extra_models_payload(deployments: list[dict]) -> dict:
    """Render a full JSON payload from deployment inventory."""
    entries = sorted(
        (build_extra_model_entry(deployment) for deployment in deployments),
        key=lambda item: item["name"].lower(),
    )
    return {"models": entries}


def render_extra_models_payload(payload: dict) -> str:
    """Serialize a payload to JSON with a trailing newline."""
    return json.dumps(payload, indent=2) + "\n"


def load_deployments_from_file(path: Path) -> list[dict]:
    """Read deployment inventory from a saved JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Deployment input JSON must be a list")
    return data


def load_deployments_from_az(account: str, resource_group: str) -> list[dict]:
    """Load deployment inventory from Azure CLI."""
    cmd = [
        "az",
        "cognitiveservices",
        "account",
        "deployment",
        "list",
        "--name",
        account,
        "--resource-group",
        resource_group,
        "--output",
        "json",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    if not isinstance(data, list):
        raise ValueError("Azure CLI returned a non-list deployment payload")
    return data


def resolve_output_path(output_path: Path | None = None) -> Path:
    """Resolve the destination path for synced models."""
    if output_path is None:
        return DEFAULT_MODELS_PATH
    return output_path.expanduser()


def write_extra_models_payload(payload: dict, output_path: Path) -> Path:
    """Write the rendered payload to disk, creating parent directories."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_extra_models_payload(payload), encoding="utf-8")
    return output_path


def auto_load_candidate_paths(
    *,
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    """Return the runtime extra-model search order."""
    if environ is None:
        environ = os.environ

    candidates: list[Path] = []
    extra_path = environ.get("CROWE_LOGIC_EXTRA_MODELS_PATH", "").strip()
    if extra_path:
        candidates.append(Path(extra_path).expanduser())

    if project_root is not None:
        candidates.append(project_root.expanduser() / "config" / "models.extra.json")

    candidates.extend([DEFAULT_MODELS_PATH, LEGACY_MODELS_PATH])
    return candidates


def sync_output_warnings(
    output_path: Path,
    *,
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Explain whether a synced file will actually be auto-loaded."""
    resolved_output = output_path.expanduser()
    candidates = auto_load_candidate_paths(project_root=project_root, environ=environ)
    warnings: list[str] = []

    if resolved_output not in candidates:
        warnings.append(
            "This path is not auto-loaded. Set CROWE_LOGIC_EXTRA_MODELS_PATH to it "
            "or move it to config/models.extra.json or ~/.config/crowe-logic/models.extra.json."
        )
        return warnings

    target_idx = candidates.index(resolved_output)
    for higher_precedence_path in candidates[:target_idx]:
        if higher_precedence_path.exists():
            warnings.append(
                f"Runtime will prefer {higher_precedence_path} over {resolved_output}."
            )

    return warnings


def parse_sync_source(
    *,
    input_path: Path | None,
    account: str | None,
    resource_group: str | None,
) -> list[dict]:
    """Load deployment inventory from a file or Azure CLI."""
    if input_path is not None:
        return load_deployments_from_file(input_path)
    if account and resource_group:
        return load_deployments_from_az(account, resource_group)
    raise ValueError("Provide either an input file or both account and resource group")


def cli_main(argv: list[str] | None = None) -> int:
    """Standalone script entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Read deployment list from a JSON file")
    parser.add_argument("--account", help="Azure Cognitive Services account name")
    parser.add_argument("--resource-group", help="Azure resource group for the account")
    parser.add_argument(
        "--output",
        type=Path,
        help=f"Write the generated JSON to a file (default: {DEFAULT_MODELS_PATH})",
    )
    args = parser.parse_args(argv)

    deployments = parse_sync_source(
        input_path=args.input,
        account=args.account,
        resource_group=args.resource_group,
    )
    payload = build_extra_models_payload(deployments)

    if args.output:
        write_extra_models_payload(payload, resolve_output_path(args.output))
    else:
        sys.stdout.write(render_extra_models_payload(payload))

    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
