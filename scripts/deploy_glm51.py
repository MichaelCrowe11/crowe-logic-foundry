#!/usr/bin/env python3
"""
CroweLM Dense — GLM 5.1 Azure ML Deployment Script

Registers THUDM/GLM-5.1 in the Azure ML model registry, creates a managed
online endpoint, deploys the model using the vLLM scoring script, and
prints the endpoint key + URL to update your .env.

Usage:
    python scripts/deploy_glm51.py register     # Register model from HuggingFace
    python scripts/deploy_glm51.py endpoint     # Create managed online endpoint
    python scripts/deploy_glm51.py deploy       # Deploy model to endpoint
    python scripts/deploy_glm51.py traffic      # Route 100% traffic to blue slot
    python scripts/deploy_glm51.py key          # Print endpoint URL and key
    python scripts/deploy_glm51.py delete       # Delete endpoint (stop billing)
    python scripts/deploy_glm51.py up           # Full pipeline: register → endpoint → deploy → traffic → key

Environment variables required:
    AZURE_ML_SUBSCRIPTION_ID  (or AZURE_SUBSCRIPTION_ID)
    AZURE_ML_RESOURCE_GROUP   (or AZURE_RESOURCE_GROUP)
    AZURE_ML_WORKSPACE_NAME
    AZURE_CLIENT_ID            \\
    AZURE_TENANT_ID             > for service-principal auth (optional; uses
    AZURE_CLIENT_SECRET        /  DefaultAzureCredential if not set)
    HF_TOKEN                   (optional — for gated HuggingFace models)
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ─── Constants ────────────────────────────────────────────────────────────────
HF_MODEL_ID = "THUDM/GLM-5.1"
AML_MODEL_NAME = "glm-5-1"
AML_MODEL_VERSION = "1"
ENDPOINT_NAME = "crowelm-dense-glm51"
DEPLOYMENT_NAME = "blue"

_DEPLOY_DIR = Path(__file__).resolve().parent.parent / "deploy" / "azure_ml"
_ENDPOINT_YAML = _DEPLOY_DIR / "glm51_endpoint.yaml"
_DEPLOYMENT_YAML = _DEPLOY_DIR / "glm51_deployment.yaml"


def _get_ml_client():
    """Return an authenticated azure.ai.ml.MLClient."""
    try:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        print("ERROR: azure-ai-ml and azure-identity are required.")
        print("  pip install azure-ai-ml azure-identity")
        sys.exit(1)

    subscription_id = (
        os.environ.get("AZURE_ML_SUBSCRIPTION_ID")
        or os.environ.get("AZURE_SUBSCRIPTION_ID")
    )
    resource_group = (
        os.environ.get("AZURE_ML_RESOURCE_GROUP")
        or os.environ.get("AZURE_RESOURCE_GROUP")
    )
    workspace_name = os.environ.get("AZURE_ML_WORKSPACE_NAME")

    missing = [
        k for k, v in {
            "AZURE_ML_SUBSCRIPTION_ID": subscription_id,
            "AZURE_ML_RESOURCE_GROUP": resource_group,
            "AZURE_ML_WORKSPACE_NAME": workspace_name,
        }.items() if not v
    ]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    return MLClient(
        credential=DefaultAzureCredential(),
        subscription_id=subscription_id,
        resource_group_name=resource_group,
        workspace_name=workspace_name,
    )


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_register(_args):
    """Register THUDM/GLM-5.1 in the Azure ML model registry."""
    from azure.ai.ml.entities import Model
    from azure.ai.ml.constants import AssetTypes

    ml = _get_ml_client()
    print(f"\nRegistering {HF_MODEL_ID} → {AML_MODEL_NAME}:{AML_MODEL_VERSION}")

    model = Model(
        name=AML_MODEL_NAME,
        version=AML_MODEL_VERSION,
        type=AssetTypes.CUSTOM_MODEL,
        description="THUDM GLM-5.1 — base model for CroweLM Dense tier",
        properties={
            "hf_model_id": HF_MODEL_ID,
            "crowe_logic_tier": "dense",
        },
        tags={"base_model": HF_MODEL_ID, "serving": "vllm"},
    )

    # For HuggingFace-hosted models, Azure ML pulls directly from the Hub at
    # deployment time via the scoring script + HF_TOKEN in the environment.
    # We register a placeholder pointing at the Hub path.
    model.path = f"azureml://registries/HuggingFace/models/{HF_MODEL_ID}/versions/1"

    registered = ml.models.create_or_update(model)
    print(f"  Registered: {registered.name}:{registered.version}")
    print(f"  Asset ID:   {registered.id}")


def cmd_endpoint(_args):
    """Create the CroweLM Dense managed online endpoint."""
    from azure.ai.ml.entities import ManagedOnlineEndpoint

    ml = _get_ml_client()
    print(f"\nCreating endpoint: {ENDPOINT_NAME}")

    endpoint = ManagedOnlineEndpoint(
        name=ENDPOINT_NAME,
        description="CroweLM Dense — GLM 5.1 served via vLLM",
        auth_mode="key",
        tags={
            "crowe_logic_tier": "dense",
            "base_model": HF_MODEL_ID,
            "serving": "vllm",
        },
    )

    poller = ml.online_endpoints.begin_create_or_update(endpoint)
    print("  Waiting for endpoint provisioning…")
    result = poller.result()
    print(f"  Endpoint ready: {result.scoring_uri}")


def cmd_deploy(_args):
    """Deploy GLM 5.1 to the managed online endpoint."""
    from azure.ai.ml.entities import (
        ManagedOnlineDeployment,
        CodeConfiguration,
        Environment,
        OnlineRequestSettings,
        ProbeSettings,
    )

    ml = _get_ml_client()
    print(f"\nDeploying {AML_MODEL_NAME}:{AML_MODEL_VERSION} → {ENDPOINT_NAME}/{DEPLOYMENT_NAME}")

    deployment = ManagedOnlineDeployment(
        name=DEPLOYMENT_NAME,
        endpoint_name=ENDPOINT_NAME,
        model=f"azureml:{AML_MODEL_NAME}:{AML_MODEL_VERSION}",
        code_configuration=CodeConfiguration(
            code=str(_DEPLOY_DIR),
            scoring_script="score.py",
        ),
        environment=Environment(
            image="mcr.microsoft.com/azureml/curated/acft-hf-nlp-gpu:latest",
            inference_config={
                "liveness_route": {"path": "/health", "port": 8000},
                "readiness_route": {"path": "/health", "port": 8000},
                "scoring_route": {"path": "/v1", "port": 8000},
            },
        ),
        environment_variables={
            "MODEL_ID": HF_MODEL_ID,
            "MAX_MODEL_LEN": "32768",
            "TENSOR_PARALLEL_SIZE": "2",
            "GPU_MEMORY_UTILIZATION": "0.92",
            "DTYPE": "bfloat16",
            "VLLM_PORT": "8000",
            "VLLM_NO_USAGE_STATS": "1",
            **({"HF_TOKEN": os.environ["HF_TOKEN"]} if os.environ.get("HF_TOKEN") else {}),
        },
        instance_type="Standard_NC96ads_A100_v4",
        instance_count=1,
        request_settings=OnlineRequestSettings(
            request_timeout_ms=120_000,
            max_concurrent_requests_per_instance=32,
            max_queue_wait_ms=30_000,
        ),
        liveness_probe=ProbeSettings(
            initial_delay=240,
            period=30,
            timeout=10,
            success_threshold=1,
            failure_threshold=5,
        ),
        readiness_probe=ProbeSettings(
            initial_delay=240,
            period=15,
            timeout=10,
            success_threshold=1,
            failure_threshold=10,
        ),
    )

    poller = ml.online_deployments.begin_create_or_update(deployment)
    print("  Deploying (this takes ~15–25 minutes for GPU provisioning)…")
    result = poller.result()
    print(f"  Deployment ready: {result.provisioning_state}")


def cmd_traffic(_args):
    """Route 100% of traffic to the blue deployment slot."""
    ml = _get_ml_client()
    print(f"\nRouting 100% traffic → {ENDPOINT_NAME}/{DEPLOYMENT_NAME}")

    endpoint = ml.online_endpoints.get(ENDPOINT_NAME)
    endpoint.traffic = {DEPLOYMENT_NAME: 100}
    poller = ml.online_endpoints.begin_create_or_update(endpoint)
    poller.result()
    print("  Traffic updated.")


def cmd_key(_args):
    """Print the endpoint URL and primary key for .env setup."""
    ml = _get_ml_client()
    print(f"\nEndpoint details for {ENDPOINT_NAME}:")

    endpoint = ml.online_endpoints.get(ENDPOINT_NAME)
    keys = ml.online_endpoints.get_keys(ENDPOINT_NAME)

    scoring_uri = (endpoint.scoring_uri or "").rstrip("/")
    # Normalize: Azure ML scoring URIs typically end in /score, while the
    # provider expects the OpenAI-compatible base URL rooted at /v1.
    base_uri = scoring_uri.removesuffix("/score")
    if base_uri and not base_uri.endswith("/v1"):
        base_uri = f"{base_uri}/v1"

    print(f"\n  AZURE_GLM51_ENDPOINT={base_uri}")
    print(f"  AZURE_GLM51_API_KEY={keys.primary_key}")
    print("\n  Add these to your .env file to activate CroweLM Dense (GLM 5.1).")


def cmd_delete(_args):
    """Delete the endpoint and stop all billing."""
    ml = _get_ml_client()
    print(f"\nDeleting endpoint: {ENDPOINT_NAME}")
    confirm = input("  This will stop all deployments and billing. Confirm (yes/no): ")
    if confirm.strip().lower() != "yes":
        print("  Aborted.")
        return
    poller = ml.online_endpoints.begin_delete(ENDPOINT_NAME)
    poller.result()
    print("  Endpoint deleted.")


def cmd_up(args):
    """Full pipeline: register → endpoint → deploy → traffic → key."""
    cmd_register(args)
    cmd_endpoint(args)
    cmd_deploy(args)
    cmd_traffic(args)
    cmd_key(args)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Deploy CroweLM Dense (GLM 5.1) to Azure ML managed online endpoint"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register", help="Register THUDM/GLM-5.1 in Azure ML model registry")
    sub.add_parser("endpoint", help="Create the managed online endpoint")
    sub.add_parser("deploy",   help="Deploy GLM 5.1 to the endpoint")
    sub.add_parser("traffic",  help="Route 100%% traffic to the blue slot")
    sub.add_parser("key",      help="Print endpoint URL and API key")
    sub.add_parser("delete",   help="Delete the endpoint")
    sub.add_parser("up",       help="Full pipeline (register → endpoint → deploy → traffic → key)")

    args = parser.parse_args()
    {
        "register": cmd_register,
        "endpoint": cmd_endpoint,
        "deploy":   cmd_deploy,
        "traffic":  cmd_traffic,
        "key":      cmd_key,
        "delete":   cmd_delete,
        "up":       cmd_up,
    }[args.command](args)


if __name__ == "__main__":
    main()
