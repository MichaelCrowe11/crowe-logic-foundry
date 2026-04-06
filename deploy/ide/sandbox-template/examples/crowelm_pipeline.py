"""
CroweLM Pipeline Demo — shows the staging pipeline flow.

This is a read-only demonstration. Sandbox users cannot write
to the production staging directory.
"""
import json


def main():
    print("=== CroweLM Staging Pipeline ===\n")
    print("The pipeline processes training data through 4 stages:\n")

    stages = [
        ("pending",  "New items awaiting evaluation"),
        ("approved", "Score >= 0.85 — auto-approved for training"),
        ("review",   "Score 0.50-0.84 — needs human review"),
        ("rejected", "Score < 0.50 — filtered out"),
    ]

    for stage, desc in stages:
        print(f"  {stage:12s} {desc}")

    print()
    print("Example item flowing through the pipeline:\n")

    example = {
        "instruction": "How do I grow shiitake mushrooms?",
        "response": "Use hardwood sawdust blocks supplemented with wheat bran...",
        "category": "mycology",
        "confidence": 0.92,
    }
    print(json.dumps(example, indent=2))
    print()
    print("This item would score >= 0.85 and be auto-approved.")


if __name__ == "__main__":
    main()
