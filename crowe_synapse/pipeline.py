"""
Crowe-Synapse Pipeline Engine — step execution with state passing.

Supports two modes:
- Agent-directed: model decides each step, engine tracks state and retries
- Framework-directed: registered templates run without model round-trips
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

import yaml


@dataclass
class StepResult:
    tool_name: str
    output: str
    status: str  # "success" or "failed"
    duration_ms: int = 0
    attempt: int = 1


@dataclass
class PipelineStep:
    tool_name: str
    input_args: dict = field(default_factory=dict)
    max_retries: int = 1
    validator: str | None = None

    def execute(self, tool_map: dict, context: dict) -> StepResult:
        resolved_args = _resolve_args(self.input_args, context)
        func = tool_map.get(self.tool_name)
        if not func:
            return StepResult(
                tool_name=self.tool_name,
                output=json.dumps({"error": f"Unknown tool: {self.tool_name}"}),
                status="failed",
            )

        last_error = None
        start = time.monotonic()
        for attempt in range(1, self.max_retries + 1):
            start = time.monotonic()
            try:
                result = func(**resolved_args)
                output = str(result) if result is not None else ""
                duration = int((time.monotonic() - start) * 1000)
                return StepResult(
                    tool_name=self.tool_name,
                    output=output,
                    status="success",
                    duration_ms=duration,
                    attempt=attempt,
                )
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(0.1 * attempt)

        duration = int((time.monotonic() - start) * 1000)
        return StepResult(
            tool_name=self.tool_name,
            output=json.dumps({"error": str(last_error)}),
            status="failed",
            duration_ms=duration,
            attempt=self.max_retries,
        )


@dataclass
class PipelineRun:
    name: str
    steps: list[PipelineStep]
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"
    results: list[StepResult] = field(default_factory=list)

    def execute(self, tool_map: dict):
        self.status = "running"
        context = {}
        for i, step in enumerate(self.steps):
            result = step.execute(tool_map, context)
            self.results.append(result)

            if result.status == "success":
                try:
                    parsed = json.loads(result.output)
                    context["previous"] = parsed
                except (json.JSONDecodeError, TypeError):
                    context["previous"] = {"raw": result.output}
                context[f"step_{i}"] = context["previous"]
            else:
                self.status = "failed"
                return

        self.status = "completed"


@dataclass
class PipelineTemplate:
    name: str
    description: str
    trigger: str | None
    steps: list[dict]

    def matches(self, text: str) -> bool:
        if not self.trigger:
            return False
        return bool(re.search(self.trigger, text, re.IGNORECASE))

    def to_pipeline_run(self) -> PipelineRun:
        steps = []
        for s in self.steps:
            steps.append(PipelineStep(
                tool_name=s["tool"],
                input_args=s.get("input_args", {}),
                max_retries=s.get("max_retries", 1),
            ))
        return PipelineRun(name=self.name, steps=steps)


class PipelineEngine:
    def __init__(self, templates_dir: str = ""):
        self._templates: list[PipelineTemplate] = []
        if templates_dir and os.path.isdir(templates_dir):
            self._load_templates(templates_dir)

    def _load_templates(self, templates_dir: str):
        for filename in sorted(os.listdir(templates_dir)):
            if filename.endswith((".yaml", ".yml")):
                path = os.path.join(templates_dir, filename)
                with open(path) as f:
                    data = yaml.safe_load(f)
                if data:
                    self._templates.append(PipelineTemplate(
                        name=data.get("name", filename),
                        description=data.get("description", ""),
                        trigger=data.get("trigger"),
                        steps=data.get("steps", []),
                    ))

    def list_templates(self) -> list[PipelineTemplate]:
        return list(self._templates)

    def get_template(self, name: str) -> PipelineTemplate | None:
        for t in self._templates:
            if t.name == name:
                return t
        return None

    def match_template(self, text: str) -> PipelineTemplate | None:
        for t in self._templates:
            if t.matches(text):
                return t
        return None


def _resolve_args(args: dict, context: dict) -> dict:
    """Replace {previous.key} and {step_N.key} placeholders with context values."""
    resolved = {}
    for k, v in args.items():
        if isinstance(v, str) and "{" in v:
            for ctx_key, ctx_val in context.items():
                if isinstance(ctx_val, dict):
                    for inner_key, inner_val in ctx_val.items():
                        placeholder = f"{{{ctx_key}.{inner_key}}}"
                        if placeholder in v:
                            v = v.replace(placeholder, str(inner_val))
            resolved[k] = v
        else:
            resolved[k] = v
    return resolved
