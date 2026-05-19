#!/usr/bin/env python3
"""
Crowe Logic Agent: Create and Configure

Creates the Crowe Logic agent on Azure AI Foundry with all available tools:
- Custom function tools (filesystem, shell, browser, search)
- Code Interpreter (sandboxed Python execution)
- Bing Grounding (live web search via Azure)
- File Search (RAG over uploaded documents)
- Azure AI Search (vector search over knowledge base)

Usage:
    python scripts/create_agent.py
    python scripts/create_agent.py --name "crowe-logic-v2" --verbose
"""

import os
import sys
import json
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import (
    CodeInterpreterTool,
    BingGroundingTool,
    AzureAISearchTool,
    AzureAISearchQueryType,
    FunctionTool,
    ToolSet,
)
from azure.identity import DefaultAzureCredential

from config.agent_config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    SYSTEM_INSTRUCTIONS,
    AGENT_NAME,
    AGENT_VERSION,
    BING_CONNECTION_ID,
    AI_SEARCH_CONNECTION_ID,
    AI_SEARCH_INDEX_NAME,
)
from tools import user_functions


def create_agent(name: str = AGENT_NAME, verbose: bool = False):
    """Create the Crowe Logic agent with all available tools."""

    print(f"  Connecting to Azure AI Foundry at {PROJECT_ENDPOINT}")
    client = AgentsClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    # Build the toolset with all available tools
    toolset = ToolSet()

    # 1. Custom function tools (Claude-like local capabilities)
    functions = FunctionTool(user_functions)
    toolset.add(functions)
    print("  Added custom function tools (filesystem, shell, browser, search)")

    # 2. Code Interpreter (sandboxed Python)
    code_interpreter = CodeInterpreterTool()
    toolset.add(code_interpreter)
    print("  Added Code Interpreter")

    # Enable auto-execution of function calls
    client.enable_auto_function_calls(toolset)

    # Build additional tools list (for tools that use tools+tool_resources pattern)
    extra_tools = []
    extra_resources = {}

    # 3. Bing Grounding (web search via Azure connection)
    if BING_CONNECTION_ID:
        bing = BingGroundingTool(connection_id=BING_CONNECTION_ID)
        extra_tools.extend(bing.definitions)
        print(f"  Added Bing Grounding (connection: {BING_CONNECTION_ID[:20]}...)")
    else:
        print("  Skipped Bing Grounding (no AZURE_BING_CONNECTION_ID set)")

    # 4. Azure AI Search (vector search over knowledge base)
    if AI_SEARCH_CONNECTION_ID:
        ai_search = AzureAISearchTool(
            index_connection_id=AI_SEARCH_CONNECTION_ID,
            index_name=AI_SEARCH_INDEX_NAME,
            query_type=AzureAISearchQueryType.SEMANTIC,
            top_k=5,
        )
        extra_tools.extend(ai_search.definitions)
        if hasattr(ai_search, "resources") and ai_search.resources:
            extra_resources.update(ai_search.resources)
        print(f"  Added Azure AI Search (index: {AI_SEARCH_INDEX_NAME})")
    else:
        print("  Skipped Azure AI Search (no AI_AZURE_AI_CONNECTION_ID set)")

    # Create the agent
    print(f"\n  Creating agent '{name}' with model '{MODEL_DEPLOYMENT_NAME}'...")

    create_kwargs = dict(
        model=MODEL_DEPLOYMENT_NAME,
        name=name,
        instructions=SYSTEM_INSTRUCTIONS,
        toolset=toolset,
    )

    # Add extra tools if any connection-based tools were configured
    if extra_tools:
        create_kwargs["tools"] = extra_tools
    if extra_resources:
        create_kwargs["tool_resources"] = extra_resources

    agent = client.create_agent(**create_kwargs)

    print("\n  Agent created successfully!")
    print(f"  Agent ID:   {agent.id}")
    print(f"  Agent Name: {agent.name}")
    print(f"  Model:      {agent.model}")

    # Save agent ID for CLI use
    agent_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".agent_id"
    )
    with open(agent_file, "w") as f:
        json.dump(
            {
                "agent_id": agent.id,
                "name": name,
                "version": AGENT_VERSION,
                "model": MODEL_DEPLOYMENT_NAME,
            },
            f,
            indent=2,
        )
    print("  Saved agent ID to .agent_id")

    if verbose:
        print("\n  Full agent config:")
        print(f"  Instructions: {SYSTEM_INSTRUCTIONS[:200]}...")
        print(
            f"  Tools: {[t.__class__.__name__ for t in [functions, code_interpreter]]}"
        )

    return agent


def main():
    parser = argparse.ArgumentParser(
        description="Create the Crowe Logic agent on Azure AI Foundry"
    )
    parser.add_argument("--name", default=AGENT_NAME, help="Agent name")
    parser.add_argument("--model", default=None, help="Override model deployment name")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.model:
        global MODEL_DEPLOYMENT_NAME
        MODEL_DEPLOYMENT_NAME = args.model

    print(f"\n{'=' * 60}")
    print("  CROWE LOGIC AGENT: CREATE")
    print(f"  Version {AGENT_VERSION}")
    print(f"{'=' * 60}\n")

    try:
        create_agent(name=args.name, verbose=args.verbose)
        print(f"\n{'=' * 60}")
        print("  READY. Run: crowe-logic chat")
        print(f"{'=' * 60}\n")
    except Exception as e:
        print(f"\n  ERROR: {e}")
        print("  Make sure you've run: az login")
        print("  And set PROJECT_ENDPOINT in .env")
        sys.exit(1)


if __name__ == "__main__":
    main()
