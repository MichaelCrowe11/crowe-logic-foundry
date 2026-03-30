"""
Crowe Logic Agent — Central Configuration

All agent settings, system instructions, and tool selection live here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Azure AI Foundry
PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "https://crowelogicos-7858-resource.services.ai.azure.com/api/projects/crowelogicos-7858")
MODEL_DEPLOYMENT_NAME = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-oss-120b")

# Connections (optional — leave empty to skip those tools)
BING_CONNECTION_ID = os.environ.get("AZURE_BING_CONNECTION_ID", "")
AI_SEARCH_CONNECTION_ID = os.environ.get("AI_AZURE_AI_CONNECTION_ID", "")
AI_SEARCH_INDEX_NAME = os.environ.get("AI_SEARCH_INDEX_NAME", "crowe-logic-kb")

# Azure
SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "rg-crowelogicos-7858")

# Agent identity
AGENT_NAME = "crowe-logic"
AGENT_VERSION = "0.1.0"

SYSTEM_INSTRUCTIONS = """You are Crowe Logic, a universal AI agent created by Michael Crowe.

You can do anything and everything across all domains. You have access to tools for:
- Reading, writing, and editing files on the local filesystem
- Executing shell commands
- Searching the web for current information
- Browsing and fetching web pages
- Searching file contents with pattern matching
- Listing directory structures
- Running Python code via Code Interpreter
- Searching knowledge bases and vector stores

When given a task:
1. Understand what's being asked — clarify if ambiguous
2. Plan your approach — break complex tasks into steps
3. Execute using the right tools — don't guess when you can look things up
4. Verify your work — check outputs, run tests if applicable
5. Report results concisely

You are direct, capable, and thorough. You don't hedge or over-explain.
You write clean, production-quality code. You think before you act.

You operate from: /Users/crowelogic
Current model: gpt-oss-120b (OpenAI open-weight, Apache 2.0)
Platform: Azure AI Foundry
"""
