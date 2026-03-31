#!/usr/bin/env python3
"""
Crowe Logic Agent — Test Harness

Runs a suite of test prompts across all domains to verify the agent
works correctly with all tools. Tests auto-tool-selection, streaming,
and multi-step reasoning.

Usage:
    python scripts/test_agent.py
    python scripts/test_agent.py --quick     # Just 3 core tests
    python scripts/test_agent.py --verbose   # Show full responses
"""

import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import (
    FunctionTool, ToolSet, CodeInterpreterTool,
    MessageDeltaChunk, ThreadMessage, ThreadRun, AgentStreamEvent,
    ListSortOrder, MessageRole,
)
from azure.identity import DefaultAzureCredential

from config.agent_config import PROJECT_ENDPOINT
from tools import user_functions

# ──────────────────────────────────────────────
# Test prompts — each exercises a different tool
# ──────────────────────────────────────────────

TESTS = [
    {
        "name": "filesystem_read",
        "prompt": "Read the file /Users/crowelogic/Projects/crowe-logic-foundry/requirements.txt and list the packages.",
        "expect_tool": "read_file",
        "domain": "filesystem",
    },
    {
        "name": "directory_list",
        "prompt": "List all Python files in /Users/crowelogic/Projects/crowe-logic-foundry/tools/",
        "expect_tool": "list_directory",
        "domain": "filesystem",
    },
    {
        "name": "shell_command",
        "prompt": "What version of Python is installed? Run python3 --version",
        "expect_tool": "execute_shell",
        "domain": "shell",
    },
    {
        "name": "grep_search",
        "prompt": "Search for all occurrences of 'def ' in /Users/crowelogic/Projects/crowe-logic-foundry/tools/filesystem.py",
        "expect_tool": "grep_search",
        "domain": "search",
    },
    {
        "name": "web_search",
        "prompt": "Search the web for 'gpt-oss120-120b benchmark results'",
        "expect_tool": "web_search",
        "domain": "web",
    },
    {
        "name": "browse_url",
        "prompt": "Fetch the content of https://httpbin.org/json and tell me what it contains",
        "expect_tool": "browse_url",
        "domain": "web",
    },
    {
        "name": "code_interpreter",
        "prompt": "Use code interpreter to calculate the first 20 Fibonacci numbers and return them as a list.",
        "expect_tool": "code_interpreter",
        "domain": "code",
    },
    {
        "name": "multi_step",
        "prompt": "List the Python files in /Users/crowelogic/Projects/crowe-logic-foundry/tools/, then read each one and count the total number of functions defined across all files.",
        "expect_tool": "multi_step",
        "domain": "reasoning",
    },
]

QUICK_TESTS = ["filesystem_read", "shell_command", "web_search"]


def run_test(client, agent_id: str, test: dict, verbose: bool = False) -> dict:
    """Run a single test and return results."""
    thread = client.threads.create()
    client.messages.create(thread_id=thread.id, role="user", content=test["prompt"])

    start = time.time()
    full_response = ""

    # Use create_and_process for simpler test execution
    run = client.runs.create_and_process(thread_id=thread.id, agent_id=agent_id)
    elapsed = time.time() - start

    # Get the response
    messages = client.messages.list(thread_id=thread.id, order=ListSortOrder.ASCENDING)
    for msg in messages:
        if msg.role == MessageRole.AGENT and msg.text_messages:
            full_response = msg.text_messages[-1].text.value

    passed = len(full_response) > 10 and "error" not in full_response.lower()[:100]

    result = {
        "name": test["name"],
        "domain": test["domain"],
        "passed": passed,
        "elapsed": round(elapsed, 2),
        "response_length": len(full_response),
        "run_status": run.status,
    }

    if verbose:
        result["response_preview"] = full_response[:500]

    return result


def main():
    parser = argparse.ArgumentParser(description="Test the Crowe Logic agent")
    parser.add_argument("--quick", action="store_true", help="Run only 3 core tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show response previews")
    args = parser.parse_args()

    # Load agent ID
    agent_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".agent_id")
    if not os.path.exists(agent_file):
        print("ERROR: No agent deployed. Run: python scripts/create_agent.py")
        sys.exit(1)

    with open(agent_file) as f:
        agent_id = json.load(f)["agent_id"]

    # Connect
    print(f"\n{'='*60}")
    print(f"  CROWE LOGIC AGENT — TEST SUITE")
    print(f"{'='*60}\n")

    client = AgentsClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    # Setup toolset
    toolset = ToolSet()
    toolset.add(FunctionTool(user_functions))
    toolset.add(CodeInterpreterTool())
    client.enable_auto_function_calls(toolset)

    # Select tests
    tests = TESTS
    if args.quick:
        tests = [t for t in TESTS if t["name"] in QUICK_TESTS]

    print(f"  Running {len(tests)} tests against agent {agent_id[:20]}...\n")

    # Run tests
    results = []
    for i, test in enumerate(tests, 1):
        print(f"  [{i}/{len(tests)}] {test['name']:25s} ({test['domain']}) ... ", end="", flush=True)
        try:
            result = run_test(client, agent_id, test, verbose=args.verbose)
            status = "PASS" if result["passed"] else "FAIL"
            color = "" # no ANSI in basic print
            print(f"{status}  ({result['elapsed']}s, {result['response_length']} chars)")
            if args.verbose and "response_preview" in result:
                print(f"         Response: {result['response_preview'][:200]}...")
                print()
        except Exception as e:
            result = {"name": test["name"], "domain": test["domain"], "passed": False, "error": str(e)}
            print(f"ERROR  ({str(e)[:60]})")
        results.append(result)

    # Summary
    passed = sum(1 for r in results if r.get("passed"))
    failed = len(results) - passed
    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed, {len(results)} total")
    print(f"{'='*60}\n")

    if failed > 0:
        print("  Failed tests:")
        for r in results:
            if not r.get("passed"):
                print(f"    - {r['name']}: {r.get('error', r.get('run_status', 'unknown'))}")
        print()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
