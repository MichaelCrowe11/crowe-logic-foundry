#!/usr/bin/env python3
"""
Crowe Logic Agent — Fine-Tuning Pipeline

Converts CroweLM unified datasets to Azure AI Foundry / Azure ML format and
initiates fine-tuning of supported base models with domain knowledge:
  - Biotech / Pharma / Drug Discovery
  - Mycology / Mushroom Cultivation
  - Molecular Biology / Gene/RNA/Protein
  - Scientific Coding / Reasoning

Supported base models:
  gpt-oss-120b   — Azure AI Foundry managed fine-tuning (OpenAI-compatible FT API)
  glm-5.1        — Azure ML LoRA fine-tuning job (THUDM/GLM-5.1 via HuggingFace)

Usage:
    python scripts/fine_tune.py convert     # Convert datasets to Azure format
    python scripts/fine_tune.py upload       # Upload to Azure AI Foundry
    python scripts/fine_tune.py train        # Start fine-tuning job
    python scripts/fine_tune.py train --base glm-5.1  # Fine-tune GLM 5.1 via Azure ML
    python scripts/fine_tune.py status       # Check training status
    python scripts/fine_tune.py pipeline     # Run full pipeline
"""

import os
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.agent_config import PROJECT_ENDPOINT

# Dataset paths
DATA_DIR = Path(__file__).parent.parent / "data"
CROWELM_UNIFIED = DATA_DIR / "crowelm-unified"
CROWELM_BIOTECH = DATA_DIR / "crowelm-biotech"
OUTPUT_DIR = DATA_DIR / "azure-ft"


def convert_nemo_sft_to_openai(input_path: Path, output_path: Path, max_samples: int = 0):
    """
    Convert NeMo SFT JSONL to OpenAI chat completion format for Azure fine-tuning.

    NeMo SFT format:
        {"conversations": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}

    Azure/OpenAI format:
        {"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
    """
    print(f"  Converting: {input_path.name}")

    system_msg = {
        "role": "system",
        "content": (
            "You are CroweLM, an expert AI assistant specializing in biotech, "
            "pharmaceutical science, mycology, mushroom cultivation, molecular biology, "
            "drug discovery, and scientific research. You provide accurate, detailed, "
            "and actionable information grounded in scientific literature and practical experience. "
            "Created by Crowe Logic, Inc."
        )
    }

    converted = 0
    skipped = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(input_path, "r", encoding="utf-8") as fin, \
         open(output_path, "w", encoding="utf-8") as fout:
        for line in fin:
            if max_samples and converted >= max_samples:
                break
            try:
                entry = json.loads(line.strip())

                # Handle different input formats
                messages = [system_msg]

                if "conversations" in entry:
                    # NeMo SFT format
                    for turn in entry["conversations"]:
                        role = turn.get("role", turn.get("from", "user"))
                        content = turn.get("content", turn.get("value", ""))
                        if role in ("human", "user"):
                            messages.append({"role": "user", "content": content})
                        elif role in ("gpt", "assistant", "model"):
                            messages.append({"role": "assistant", "content": content})

                elif "instruction" in entry and "output" in entry:
                    # Instruction format
                    user_content = entry["instruction"]
                    if entry.get("input"):
                        user_content += f"\n\n{entry['input']}"
                    messages.append({"role": "user", "content": user_content})
                    messages.append({"role": "assistant", "content": entry["output"]})

                elif "question" in entry and "answer" in entry:
                    # QA format
                    messages.append({"role": "user", "content": entry["question"]})
                    messages.append({"role": "assistant", "content": entry["answer"]})

                elif "text" in entry:
                    # Pretraining text — wrap as a knowledge entry
                    messages.append({"role": "user", "content": "Explain the following topic in detail."})
                    messages.append({"role": "assistant", "content": entry["text"]})

                else:
                    skipped += 1
                    continue

                # Validate: must have at least system + user + assistant
                if len(messages) >= 3:
                    fout.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
                    converted += 1
                else:
                    skipped += 1

            except (json.JSONDecodeError, KeyError):
                skipped += 1

    print(f"    Converted: {converted:,} | Skipped: {skipped:,}")
    return converted


def cmd_convert(args):
    """Convert all CroweLM datasets to Azure fine-tuning format."""
    print(f"\n{'='*60}")
    print("  DATASET CONVERSION — CroweLM → Azure FT Format")
    print(f"{'='*60}\n")

    total = 0

    # Find all JSONL files in the unified dataset
    jsonl_files = list(CROWELM_UNIFIED.rglob("*.jsonl"))
    if not jsonl_files:
        print("  No JSONL files found in data/crowelm-unified/")
        print("  Looking in Azure blob storage...")
        # Try downloading from Azure
        print("  Run: az storage blob download-batch --account-name crowelmdata7595 --source nvidia-curated-datasets -d data/crowelm-unified/")
        return

    for jsonl_file in sorted(jsonl_files):
        output_name = f"azure_ft_{jsonl_file.stem}.jsonl"
        output_path = OUTPUT_DIR / output_name
        count = convert_nemo_sft_to_openai(
            jsonl_file, output_path,
            max_samples=args.max_samples if hasattr(args, 'max_samples') else 0
        )
        total += count

    print(f"\n  Total converted: {total:,} samples")
    print(f"  Output directory: {OUTPUT_DIR}")

    # Create a merged training file
    merged_path = OUTPUT_DIR / "crowelm_merged_train.jsonl"
    print(f"\n  Merging all files into: {merged_path.name}")
    with open(merged_path, "w") as fout:
        for ft_file in sorted(OUTPUT_DIR.glob("azure_ft_*.jsonl")):
            with open(ft_file) as fin:
                for line in fin:
                    fout.write(line)

    line_count = sum(1 for _ in open(merged_path))
    print(f"  Merged file: {line_count:,} samples")


def cmd_upload(args):
    """Upload converted dataset to Azure AI Foundry."""
    print(f"\n{'='*60}")
    print("  DATASET UPLOAD — Azure AI Foundry")
    print(f"{'='*60}\n")

    from azure.ai.agents import AgentsClient
    from azure.ai.agents.models import FilePurpose
    from azure.identity import DefaultAzureCredential

    merged_path = OUTPUT_DIR / "crowelm_merged_train.jsonl"
    if not merged_path.exists():
        print("  ERROR: Run 'convert' first to generate the training file.")
        return

    client = AgentsClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    print(f"  Uploading: {merged_path.name}")
    file = client.files.upload_and_poll(
        file_path=str(merged_path),
        purpose=FilePurpose.AGENTS,
    )
    print(f"  Uploaded! File ID: {file.id}")

    # Save file ID for training
    meta_path = OUTPUT_DIR / "upload_meta.json"
    with open(meta_path, "w") as f:
        json.dump({"file_id": file.id, "filename": merged_path.name}, f, indent=2)
    print(f"  Saved metadata to: {meta_path}")


def cmd_train(args):
    """Start fine-tuning job on Azure (gpt-oss-120b or GLM 5.1 via LoRA)."""
    base_model = getattr(args, "base", "gpt-oss-120b")

    print(f"\n{'='*60}")
    print(f"  FINE-TUNING — {base_model} + CroweLM Data")
    print(f"{'='*60}\n")

    merged_path = OUTPUT_DIR / "crowelm_merged_train.jsonl"
    if merged_path.exists():
        line_count = sum(1 for _ in open(merged_path))
        size_mb = merged_path.stat().st_size / (1024 * 1024)
        print(f"  Dataset ready: {line_count:,} samples ({size_mb:.1f} MB)")
    else:
        print("  Dataset not converted yet. Run: python scripts/fine_tune.py convert")
        return

    if base_model == "glm-5.1":
        _train_glm51(merged_path)
    else:
        _train_gpt_oss(base_model)


def _train_gpt_oss(base_model: str):
    """Print gpt-oss-120b fine-tuning instructions."""
    print(f"  NOTE: {base_model} fine-tuning options:")
    print()
    print("  1. Azure AI Foundry Managed Fine-Tuning:")
    print("     az ml job create --file training_config.yaml")
    print()
    print("  2. Self-hosted via vLLM + LoRA (RunPod / local H100):")
    print("     See data/crowelm-unified/RUNPOD_TRAINING_GUIDE.md")
    print()
    print("  3. HuggingFace Transformers + PEFT:")
    print("     python scripts/hf_fine_tune.py")


def _train_glm51(dataset_path: Path):
    """Generate and optionally submit an Azure ML LoRA fine-tune job for GLM 5.1."""
    import os

    subscription_id = (
        os.environ.get("AZURE_ML_SUBSCRIPTION_ID")
        or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    )
    resource_group = (
        os.environ.get("AZURE_ML_RESOURCE_GROUP")
        or os.environ.get("AZURE_RESOURCE_GROUP", "")
    )
    workspace_name = os.environ.get("AZURE_ML_WORKSPACE_NAME", "")

    job_yaml_path = OUTPUT_DIR / "glm51_lora_job.yaml"
    job_yaml_path.parent.mkdir(parents=True, exist_ok=True)

    job_yaml = f"""\
$schema: https://azuremlschemas.azureedge.net/latest/commandJob.schema.json

display_name: crowelm-dense-glm51-lora
experiment_name: crowelm-dense-glm51

command: >-
  python -m axolotl.cli.train glm51_lora_config.yaml

environment:
  image: mcr.microsoft.com/azureml/curated/acft-hf-nlp-gpu:latest

inputs:
  train_data:
    type: uri_file
    path: {dataset_path}

compute: azureml:gpu-cluster-a100

resources:
  instance_type: Standard_NC96ads_A100_v4
  instance_count: 1

environment_variables:
  BASE_MODEL: THUDM/GLM-5.1
  HF_TOKEN: ${{{{inputs.hf_token}}}}
  LORA_RANK: "64"
  LORA_ALPHA: "128"
  MAX_SEQ_LEN: "4096"
  BATCH_SIZE: "4"
  GRAD_ACCUM: "8"
  LEARNING_RATE: "2e-4"
  EPOCHS: "3"
  OUTPUT_DIR: ./outputs/crowelm-dense-glm51-lora

tags:
  base_model: THUDM/GLM-5.1
  method: lora
  crowe_logic_tier: dense
"""

    with open(job_yaml_path, "w") as f:
        f.write(job_yaml)

    print(f"  Generated Azure ML job YAML: {job_yaml_path}")
    print()

    if subscription_id and resource_group and workspace_name:
        print("  Submitting job to Azure ML…")
        import subprocess
        result = subprocess.run(
            [
                "az", "ml", "job", "create",
                "--file", str(job_yaml_path),
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
            job = _json.loads(result.stdout)
            print(f"  Job submitted: {job.get('name', 'unknown')}")
            print(f"  Status URL:    {job.get('services', {}).get('Studio', {}).get('endpoint', '')}")
        else:
            print(f"  az ml job create failed:\n{result.stderr}")
            print("  Submit manually:")
            print(f"    az ml job create --file {job_yaml_path} \\")
            print(f"      --subscription {subscription_id} \\")
            print(f"      --resource-group {resource_group} \\")
            print(f"      --workspace-name {workspace_name}")
    else:
        print("  Azure ML workspace not configured. Submit manually:")
        print(f"    az ml job create --file {job_yaml_path} \\")
        print("      --subscription <AZURE_ML_SUBSCRIPTION_ID> \\")
        print("      --resource-group <AZURE_ML_RESOURCE_GROUP> \\")
        print("      --workspace-name <AZURE_ML_WORKSPACE_NAME>")


def cmd_status(args):
    """Check fine-tuning job status."""
    meta_path = OUTPUT_DIR / "upload_meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        print(f"  Uploaded file: {meta.get('file_id', 'unknown')}")
    else:
        print("  No upload metadata found.")


def cmd_pipeline(args):
    """Run full pipeline: convert → upload."""
    cmd_convert(args)
    cmd_upload(args)
    cmd_train(args)


def main():
    parser = argparse.ArgumentParser(description="CroweLM Fine-Tuning Pipeline")
    sub = parser.add_subparsers(dest="command")

    p_convert = sub.add_parser("convert", help="Convert datasets to Azure format")
    p_convert.add_argument("--max-samples", type=int, default=0, help="Limit samples (0=all)")

    sub.add_parser("upload", help="Upload to Azure AI Foundry")
    p_train = sub.add_parser("train", help="Start fine-tuning")
    p_train.add_argument(
        "--base",
        choices=["gpt-oss-120b", "glm-5.1"],
        default="gpt-oss-120b",
        help="Base model to fine-tune (default: gpt-oss-120b)",
    )
    sub.add_parser("status", help="Check status")
    sub.add_parser("pipeline", help="Full pipeline")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "convert": cmd_convert,
        "upload": cmd_upload,
        "train": cmd_train,
        "status": cmd_status,
        "pipeline": cmd_pipeline,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
