#!/usr/bin/env python3
"""
Crowe Logic Agent — Fine-Tuning Pipeline

Converts CroweLM unified datasets to Azure AI Foundry format and
initiates fine-tuning of gpt-oss-120b with domain knowledge:
  - Biotech / Pharma / Drug Discovery
  - Mycology / Mushroom Cultivation
  - Molecular Biology / Gene/RNA/Protein
  - Scientific Coding / Reasoning

Usage:
    python scripts/fine_tune.py convert     # Convert datasets to Azure format
    python scripts/fine_tune.py upload       # Upload to Azure AI Foundry
    python scripts/fine_tune.py train        # Start fine-tuning job
    python scripts/fine_tune.py status       # Check training status
    python scripts/fine_tune.py pipeline     # Run full pipeline
"""

import os
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.agent_config import PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME

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
    print(f"  DATASET CONVERSION — CroweLM → Azure FT Format")
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
    print(f"  DATASET UPLOAD — Azure AI Foundry")
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
    """Start fine-tuning job on Azure (placeholder — requires Azure ML or OpenAI FT API)."""
    print(f"\n{'='*60}")
    print(f"  FINE-TUNING — gpt-oss-120b + CroweLM Data")
    print(f"{'='*60}\n")

    print("  NOTE: gpt-oss-120b fine-tuning options:")
    print()
    print("  1. Azure AI Foundry Managed Fine-Tuning:")
    print("     az ml job create --file training_config.yaml")
    print()
    print("  2. Self-hosted via vLLM + LoRA (RunPod / local H100):")
    print("     See data/crowelm-unified/RUNPOD_TRAINING_GUIDE.md")
    print()
    print("  3. HuggingFace Transformers + PEFT:")
    print("     python scripts/hf_fine_tune.py")
    print()

    merged_path = OUTPUT_DIR / "crowelm_merged_train.jsonl"
    if merged_path.exists():
        line_count = sum(1 for _ in open(merged_path))
        size_mb = merged_path.stat().st_size / (1024 * 1024)
        print(f"  Dataset ready: {line_count:,} samples ({size_mb:.1f} MB)")
    else:
        print("  Dataset not converted yet. Run: python scripts/fine_tune.py convert")


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
    sub.add_parser("train", help="Start fine-tuning")
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
