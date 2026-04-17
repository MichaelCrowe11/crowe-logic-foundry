"""
CroweLM data tools — query, curate, and manage training datasets.

Reads from data/crowelm-unified/. Curated examples go to data/crowelm-unified/curated/.
"""

import glob
import json
import os
import uuid

import yaml

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.environ.get("CROWE_LOGIC_PROJECT_ROOT", _PACKAGE_ROOT)
DATA_DIR = os.path.join(_PROJECT_ROOT, "data", "crowelm-unified")


# -- Query Tier ----------------------------------------------------------------

def crowelm_list_datasets() -> str:
    """
    List all CroweLM datasets from the manifest.

    :return: JSON with dataset catalog including summary, datasets acquired, and top domains.
    :rtype: str
    """
    try:
        manifest_path = os.path.join(DATA_DIR, "DATASET_MANIFEST.json")
        if not os.path.exists(manifest_path):
            return json.dumps({"error": f"Manifest not found at {manifest_path}"})
        with open(manifest_path) as f:
            manifest = json.load(f)

        unified_path = os.path.join(DATA_DIR, "unified_training", "UNIFIED_MANIFEST.json")
        unified = {}
        if os.path.exists(unified_path):
            with open(unified_path) as f:
                unified = json.load(f)

        return json.dumps({
            "summary": manifest.get("summary", {}),
            "datasets_acquired": manifest.get("datasets_acquired", {}),
            "top_domains": manifest.get("top_domains", {}),
            "unified_training": unified,
            "curated_count": _count_curated_examples(),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_dataset_stats(dataset_name: str = "all") -> str:
    """
    Get statistics for CroweLM training datasets.

    :param dataset_name: Dataset name or "all" for aggregate stats.
    :return: JSON with row counts, domains, and curated counts.
    :rtype: str
    """
    try:
        manifest_path = os.path.join(DATA_DIR, "DATASET_MANIFEST.json")
        if not os.path.exists(manifest_path):
            return json.dumps({"error": "Manifest not found"})
        with open(manifest_path) as f:
            manifest = json.load(f)

        summary = manifest.get("summary", {})
        stats = {
            "total_raw_samples": summary.get("total_raw_samples", 0),
            "crowelm_training_entries": summary.get("crowelm_training_entries", 0),
            "total_size_gb": summary.get("total_size_gb", 0),
            "domains": summary.get("domains", ""),
            "top_domains": manifest.get("top_domains", {}),
            "curated_count": _count_curated_examples(),
        }

        if dataset_name != "all":
            acquired = manifest.get("datasets_acquired", {})
            if dataset_name in acquired:
                stats["selected_dataset"] = {dataset_name: acquired[dataset_name]}
            else:
                stats["note"] = f"Dataset '{dataset_name}' not found in manifest"

        return json.dumps(stats)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_search_examples(query: str, dataset: str = "all", limit: int = 10) -> str:
    """
    Search training examples by content (instruction + response).

    :param query: Search string (substring match).
    :param dataset: Restrict to a category or "all".
    :param limit: Max results to return.
    :return: JSON with matching examples.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        results = []
        query_lower = query.lower()

        if not os.path.exists(curated_dir):
            return json.dumps({"results": [], "count": 0, "query": query})

        pattern = f"{dataset}.jsonl" if dataset != "all" else "*.jsonl"
        for jsonl_path in glob.glob(os.path.join(curated_dir, pattern)):
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    example = json.loads(line)
                    text = (example.get("instruction", "") + " " + example.get("response", "")).lower()
                    if query_lower in text:
                        example["_source"] = os.path.basename(jsonl_path)
                        results.append(example)
                        if len(results) >= limit:
                            break
            if len(results) >= limit:
                break

        return json.dumps({"results": results, "count": len(results), "query": query})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_inspect_config() -> str:
    """
    Show current training configuration (NeMo and RunPod).

    :return: JSON with parsed training parameters.
    :rtype: str
    """
    try:
        config = {}

        nemo_path = os.path.join(DATA_DIR, "nemo_training", "training_config.yaml")
        if os.path.exists(nemo_path):
            with open(nemo_path) as f:
                config["nemo"] = yaml.safe_load(f)

        runpod_path = os.path.join(DATA_DIR, "runpod_crowelm_unified_config.yaml")
        if os.path.exists(runpod_path):
            with open(runpod_path) as f:
                config["runpod"] = yaml.safe_load(f)

        if not config:
            return json.dumps({"error": "No training configs found"})

        return json.dumps(config)
    except Exception as e:
        return json.dumps({"error": str(e)})


# -- Curation Tier -------------------------------------------------------------

def crowelm_add_example(instruction: str, response: str, category: str = "general") -> str:
    """
    Add a new training example to the curated dataset.

    :param instruction: The training prompt/instruction.
    :param response: The expected model response.
    :param category: Category for organization (general, mycology, quantum, etc.).
    :return: JSON confirmation with example ID.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        os.makedirs(curated_dir, exist_ok=True)

        example_id = str(uuid.uuid4())[:8]
        example = {
            "id": example_id,
            "instruction": instruction,
            "response": response,
            "category": category,
        }

        file_path = os.path.join(curated_dir, f"{category}.jsonl")
        with open(file_path, "a") as f:
            f.write(json.dumps(example) + "\n")

        return json.dumps({"added": True, "example_id": example_id, "category": category, "file": file_path})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_remove_example(example_id: str) -> str:
    """
    Remove a training example by ID from curated datasets.

    :param example_id: The UUID prefix of the example to remove.
    :return: JSON confirmation.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        if not os.path.exists(curated_dir):
            return json.dumps({"error": "No curated directory found"})

        for jsonl_path in glob.glob(os.path.join(curated_dir, "*.jsonl")):
            lines = []
            found = False
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    example = json.loads(line)
                    if example.get("id") == example_id:
                        found = True
                        continue
                    lines.append(line)

            if found:
                with open(jsonl_path, "w") as f:
                    for line in lines:
                        f.write(line + "\n")
                return json.dumps({"removed": True, "example_id": example_id, "file": jsonl_path})

        return json.dumps({"removed": False, "error": f"Example {example_id} not found"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_export_curated(format: str = "jsonl") -> str:
    """
    Export all curated training examples as a single merged file.

    :param format: Output format — "jsonl" (default), "nemo", or "openai".
    :return: JSON with export path and count.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        if not os.path.exists(curated_dir):
            return json.dumps({"error": "No curated directory found"})

        examples = []
        for jsonl_path in sorted(glob.glob(os.path.join(curated_dir, "*.jsonl"))):
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        examples.append(json.loads(line))

        if not examples:
            return json.dumps({"error": "No curated examples to export"})

        export_path = os.path.join(DATA_DIR, f"curated_export.{format}")

        with open(export_path, "w") as f:
            for ex in examples:
                if format == "openai":
                    chat_format = {
                        "messages": [
                            {"role": "user", "content": ex["instruction"]},
                            {"role": "assistant", "content": ex["response"]},
                        ]
                    }
                    f.write(json.dumps(chat_format) + "\n")
                elif format == "nemo":
                    nemo_format = {"input": ex["instruction"], "output": ex["response"]}
                    f.write(json.dumps(nemo_format) + "\n")
                else:
                    f.write(json.dumps(ex) + "\n")

        return json.dumps({"path": export_path, "count": len(examples), "format": format})
    except Exception as e:
        return json.dumps({"error": str(e)})


# -- Pipeline Tier -------------------------------------------------------------

def crowelm_prepare_training(config_overrides: str = "{}") -> str:
    """
    Validate curated data and prepare for training.

    :param config_overrides: JSON string of config overrides (e.g. '{"epochs": 5}').
    :return: JSON with validation results and training readiness.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        overrides = json.loads(config_overrides)

        examples = []
        issues = []
        seen_instructions = set()

        if os.path.exists(curated_dir):
            for jsonl_path in glob.glob(os.path.join(curated_dir, "*.jsonl")):
                with open(jsonl_path) as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        ex = json.loads(line)
                        if not ex.get("instruction"):
                            issues.append(f"{os.path.basename(jsonl_path)}:{line_num} — empty instruction")
                            continue
                        if not ex.get("response"):
                            issues.append(f"{os.path.basename(jsonl_path)}:{line_num} — empty response")
                            continue
                        if ex["instruction"] in seen_instructions:
                            issues.append(f"{os.path.basename(jsonl_path)}:{line_num} — duplicate instruction")
                            continue
                        seen_instructions.add(ex["instruction"])
                        examples.append(ex)

        nemo_config_path = os.path.join(DATA_DIR, "nemo_training", "training_config.yaml")
        config = {}
        if os.path.exists(nemo_config_path):
            with open(nemo_config_path) as f:
                config = yaml.safe_load(f) or {}
        config.update(overrides)

        return json.dumps({
            "ready": len(examples) > 0 and len(issues) == 0,
            "total_examples": len(examples),
            "issues": issues,
            "config": config,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_upload_dataset(target: str = "runpod") -> str:
    """
    Upload curated dataset to cloud storage for training.

    :param target: Cloud target — "runpod", "azure", or "azure_ml".
                   Use "azure_ml" to upload to an Azure ML workspace data asset
                   for GLM 5.1 LoRA fine-tuning (requires AZURE_ML_WORKSPACE_NAME,
                   AZURE_ML_SUBSCRIPTION_ID, AZURE_ML_RESOURCE_GROUP).
    :return: JSON with upload status.
    :rtype: str
    """
    try:
        export_result = json.loads(crowelm_export_curated(format="nemo"))
        if "error" in export_result:
            return json.dumps({"error": f"Export failed: {export_result['error']}"})

        export_path = export_result["path"]
        size_mb = os.path.getsize(export_path) / (1024 * 1024)

        if target == "runpod":
            upload_script = os.path.join(DATA_DIR, "upload_and_train_runpod.py")
            if not os.path.exists(upload_script):
                return json.dumps({"error": "RunPod upload script not found", "hint": "Run manually with cloud_storage_manager.py"})
            return json.dumps({
                "uploaded": False,
                "target": target,
                "size_mb": round(size_mb, 2),
                "export_path": export_path,
                "action_required": f"Run: .venv/bin/python {upload_script}",
            })
        elif target == "azure":
            return json.dumps({
                "uploaded": False,
                "target": target,
                "size_mb": round(size_mb, 2),
                "export_path": export_path,
                "action_required": "Run: .venv/bin/python cloud_storage_manager.py upload",
            })
        elif target == "azure_ml":
            import subprocess
            subscription_id = (
                os.environ.get("AZURE_ML_SUBSCRIPTION_ID")
                or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
            )
            resource_group = (
                os.environ.get("AZURE_ML_RESOURCE_GROUP")
                or os.environ.get("AZURE_RESOURCE_GROUP", "")
            )
            workspace_name = os.environ.get("AZURE_ML_WORKSPACE_NAME", "")

            if not all([subscription_id, resource_group, workspace_name]):
                return json.dumps({
                    "uploaded": False,
                    "target": target,
                    "size_mb": round(size_mb, 2),
                    "export_path": export_path,
                    "error": "AZURE_ML_WORKSPACE_NAME, AZURE_ML_SUBSCRIPTION_ID, and AZURE_ML_RESOURCE_GROUP must be set",
                    "action_required": "Set env vars then re-run, or upload manually with: az ml data create",
                })

            result = subprocess.run(
                [
                    "az", "ml", "data", "create",
                    "--name", "crowelm-dense-training",
                    "--version", "1",
                    "--type", "uri_file",
                    "--path", export_path,
                    "--subscription", subscription_id,
                    "--resource-group", resource_group,
                    "--workspace-name", workspace_name,
                    "--output", "json",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                import json as _json
                data_asset = _json.loads(result.stdout)
                return json.dumps({
                    "uploaded": True,
                    "target": target,
                    "size_mb": round(size_mb, 2),
                    "export_path": export_path,
                    "azure_ml_asset_id": data_asset.get("id", ""),
                    "next_step": "Run: python scripts/fine_tune.py train --base glm-5.1",
                })
            else:
                return json.dumps({
                    "uploaded": False,
                    "target": target,
                    "size_mb": round(size_mb, 2),
                    "export_path": export_path,
                    "error": result.stderr.strip(),
                    "action_required": (
                        f"az ml data create --name crowelm-dense-training --version 1 "
                        f"--type uri_file --path {export_path} "
                        f"--resource-group {resource_group} --workspace-name {workspace_name}"
                    ),
                })
        else:
            return json.dumps({"error": f"Unknown target: {target}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_training_status() -> str:
    """
    Check status of active CroweLM training runs.

    :return: JSON with training run status.
    :rtype: str
    """
    try:
        return json.dumps({
            "running": False,
            "message": "No active training runs detected. Use crowelm_upload_dataset to start.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# -- Internal ------------------------------------------------------------------

def _count_curated_examples() -> int:
    """Count total examples across all curated JSONL files."""
    curated_dir = os.path.join(DATA_DIR, "curated")
    if not os.path.exists(curated_dir):
        return 0
    count = 0
    for jsonl_path in glob.glob(os.path.join(curated_dir, "*.jsonl")):
        with open(jsonl_path) as f:
            count += sum(1 for line in f if line.strip())
    return count
