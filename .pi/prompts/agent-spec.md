Generate a Crowe Logic agent YAML specification from a natural language description.

Output a complete agent definition in this format:

```yaml
# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
name: {snake_case_agent_name}
description: |
  {One-line summary.}
model: crowelm-pro   # or specific model endpoint
tools:
  - list_capture_devices
  - read_file
  - execute_shell
  # ... declare only tools the agent needs
prompt_override: |
  You are the {Name} agent within the CroweLM stack.

  Operating principles:
  1. {First principle}
  2. {Second principle}
  ...
pipelines: []
```

Rules:
- **name**: Lowercase, snake_case, max 30 chars
- **description**: Max 120 chars, plain text, no markdown
- **model**: Prefer `crowelm-pro` unless the user specifies another endpoint
- **tools**: Minimal necessary set. Never blindly list all tools. Prefer the Crowe Logic native tools (`read_file`, `write_file`, `edit_file`, `execute_shell`, `run_applescript`) over generic tool duplication
- **prompt_override**: 5-10 numbered principles. Be specific about defaults, failure modes, and communication style. No emojis
- Never hardcode tenant names or project-specific paths in the prompt; instruct the agent to discover them from config
- Include `pipelines: []` if no custom pipelines are needed
