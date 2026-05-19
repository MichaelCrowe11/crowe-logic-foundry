/**
 * Crowe Logic Foundry — Pi Extension
 *
 * Integrates pi with the Crowe Logic monorepo:
 * 1. Custom tools: crowe_logic, crowe_build, crowe_agent, crowe_config
 * 2. Commands: /crowe-build, /crowe-review, /crowe-lint, /crowe-test
 * 3. Session validation: checks .venv, agents/, warns on misconfig
 * 4. Safety gates: blocks dangerous bash, protects .env
 */

import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { execSync, spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

interface BuildState {
  lastTarget?: string;
  lastStatus?: "ok" | "fail";
}

function safeSpawn(
  cmd: string,
  args: string[],
  cwd: string,
  timeout: number,
  extraEnv?: Record<string, string>
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      cwd,
      timeout,
      env: { ...process.env, ...extraEnv },
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (d) => { stdout += d; });
    child.stderr?.on("data", (d) => { stderr += d; });
    child.on("close", (code) => {
      resolve({ stdout, stderr, exitCode: code ?? 0 });
    });
    child.on("error", (err) => {
      resolve({ stdout, stderr: err.message, exitCode: 1 });
    });
  });
}

export default function croweLogicExtension(pi: ExtensionAPI) {
  const PROJECT_ROOT = process.cwd();
  const VENV_PY = resolve(PROJECT_ROOT, ".venv/bin/python");
  const PY = existsSync(VENV_PY) ? VENV_PY : "python3";

  // ── Utility ──────────────────────────────────────────────────────────

  function isInProject(): boolean {
    return existsSync(resolve(PROJECT_ROOT, "agents")) &&
           existsSync(resolve(PROJECT_ROOT, "pyproject.toml"));
  }

  function persistState(state: BuildState) {
    pi.appendEntry<BuildState>("crowe-build-state", state);
  }

  function restoreState(ctx: ExtensionContext): BuildState | undefined {
    for (const entry of ctx.sessionManager.getBranch()) {
      if (entry.type === "custom" && entry.customType === "crowe-build-state") {
        return entry.data as BuildState | undefined;
      }
    }
    return undefined;
  }

  // ── Custom Tools ───────────────────────────────────────────────────

  pi.registerTool({
    name: "crowe_logic",
    label: "Crowe Logic CLI",
    description: "Execute a Crowe Logic CLI subcommand (chat, agents, pipelines, etc.) via Python",
    parameters: Type.Object({
      command: Type.String({
        description: "CLI subcommand. Common: chat, agents, pipelines, headless",
      }),
      args: Type.Optional(Type.Array(Type.String(), {
        description: "Additional positional arguments",
      })),
    }),
    async execute(_id, params, _signal, _onUpdate, _ctx) {
      const result = await safeSpawn(
        PY,
        ["-m", "cli.crowe_logic", params.command, ...(params.args || [])],
        PROJECT_ROOT,
        120_000,
        { PYTHONPATH: PROJECT_ROOT }
      );
      return {
        content: [
          { type: "text", text: result.stdout || "(no stdout)" },
          ...(result.stderr ? [{ type: "text", text: result.stderr }] : []),
        ],
        details: { exitCode: result.exitCode },
        error: result.exitCode !== 0 ? `Exit code ${result.exitCode}` : undefined,
      };
    },
  });

  pi.registerTool({
    name: "crowe_build",
    label: "Crowe Build",
    description: "Run a Makefile target (install, lint, fmt, test, preview, prod, chat, e2e, clean)",
    parameters: Type.Object({
      target: Type.String({
        description: "Makefile target",
        examples: ["install", "lint", "fmt", "test", "preview", "prod", "chat", "e2e", "clean"],
      }),
      extra: Type.Optional(Type.String({ description: "Extra arguments passed to make" })),
    }),
    async execute(_id, params, _signal, _onUpdate, _ctx) {
      const args = params.extra ? [params.target, params.extra] : [params.target];
      const result = await safeSpawn("make", args, PROJECT_ROOT, 300_000);
      persistState({ lastTarget: params.target, lastStatus: result.exitCode === 0 ? "ok" : "fail" });
      return {
        content: [
          { type: "text", text: result.stdout || "(no stdout)" },
          ...(result.stderr ? [{ type: "text", text: result.stderr }] : []),
        ],
        details: { target: params.target, exitCode: result.exitCode },
        error: result.exitCode !== 0 ? `make ${params.target} failed` : undefined,
      };
    },
  });

  pi.registerTool({
    name: "crowe_agent",
    label: "Crowe Agent",
    description: "Run a CroweLM pipeline agent (from agents/ or tools/agent_runner)",
    parameters: Type.Object({
      agent: Type.String({
        description: "Agent name, e.g. crowelm_gen_mycology, studio, quantum",
      }),
      task: Type.String({
        description: "Task description passed to the agent",
      }),
      mode: Type.Optional(Type.String({
        enum: ["local", "docker"],
        default: "local",
        description: "Execution mode: local (subprocess) or docker (container)",
      })),
    }),
    async execute(_id, params, _signal, _onUpdate, _ctx) {
      // Run tools.agent_runner via inline Python script, avoiding shell escaping
      const script = `
import sys, json
import tools.agent_runner as ar
agent = sys.argv[1]
task = sys.argv[2]
mode = sys.argv[3]
result = ar.run_agent(agent, task, mode=mode)
print(json.dumps(result, indent=2))
`;
      const result = await safeSpawn(
        PY,
        ["-c", script, params.agent, params.task, params.mode || "local"],
        PROJECT_ROOT,
        600_000,
        { PYTHONPATH: PROJECT_ROOT }
      );
      const output = result.stdout || result.stderr || "(no output)";
      let parsed: unknown;
      try { parsed = JSON.parse(output); } catch { parsed = undefined; }
      return {
        content: [{ type: "text", text: output }],
        details: { agent: params.agent, mode: params.mode, parsed, exitCode: result.exitCode },
        error: result.exitCode !== 0 ? `Agent run failed` : undefined,
      };
    },
  });

  pi.registerTool({
    name: "crowe_config",
    label: "Crowe Config",
    description: "Read project configuration files safely (read-only). Supports .env, pyproject.toml, railway.json",
    parameters: Type.Object({
      file: Type.String({
        enum: [".env", "pyproject.toml", "railway.json", "package.json", "Makefile"],
        default: ".env",
        description: "Config file to inspect",
      }),
      grep: Type.Optional(Type.String({
        description: "If provided, return only lines matching this string (case-insensitive)",
      })),
    }),
    async execute(_id, params, _signal, _onUpdate, _ctx) {
      const path = resolve(PROJECT_ROOT, params.file);
      if (!existsSync(path)) {
        return {
          content: [{ type: "text", text: `File not found: ${params.file}` }],
          details: { file: params.file },
          error: "File not found",
        };
      }
      let text: string;
      try {
        text = readFileSync(path, "utf-8");
      } catch (e: any) {
        return {
          content: [{ type: "text", text: `Cannot read ${params.file}: ${e.message}` }],
          details: { file: params.file },
          error: e.message,
        };
      }
      if (params.grep) {
        const lines = text.split("\n").filter((l) =>
          l.toLowerCase().includes(params.grep!.toLowerCase())
        );
        text = lines.join("\n");
      }
      return {
        content: [{ type: "text", text: text }],
        details: { file: params.file, lineCount: text.split("\n").length },
      };
    },
  });

  // ── Commands ───────────────────────────────────────────────────────

  pi.registerCommand("crowe-build", {
    description: "Run a Makefile target",
    handler: async (args, ctx) => {
      const target = args.trim() || "help";
      ctx.ui.notify(`Running make ${target}...`, "info");
      const result = await safeSpawn("make", [target], PROJECT_ROOT, 300_000);
      const ok = result.exitCode === 0;
      persistState({ lastTarget: target, lastStatus: ok ? "ok" : "fail" });
      if (ok) {
        ctx.ui.notify(`✅ make ${target} succeeded`, "success");
      } else {
        ctx.ui.notify(`❌ make ${target} failed`, "error");
      }
      ctx.ui.showResult(result.stdout + "\n" + result.stderr);
    },
  });

  pi.registerCommand("crowe-lint", {
    description: "Run ruff lint/format",
    handler: async (_args, ctx) => {
      ctx.ui.notify("Running ruff check + format...", "info");
      const check = await safeSpawn("make", ["lint"], PROJECT_ROOT, 60_000);
      const fmt = await safeSpawn("make", ["fmt"], PROJECT_ROOT, 60_000);
      const ok = check.exitCode === 0 && fmt.exitCode === 0;
      ctx.ui.notify(ok ? "✅ Lint clean" : "⚠️ Lint issues found", ok ? "success" : "warning");
      ctx.ui.showResult(check.stdout + "\n" + fmt.stdout);
    },
  });

  pi.registerCommand("crowe-test", {
    description: "Run pytest",
    handler: async (_args, ctx) => {
      ctx.ui.notify("Running tests...", "info");
      const result = await safeSpawn("make", ["test"], PROJECT_ROOT, 300_000);
      const ok = result.exitCode === 0;
      ctx.ui.notify(ok ? "✅ Tests passed" : "❌ Tests failed", ok ? "success" : "error");
      ctx.ui.showResult(result.stdout + "\n" + result.stderr);
    },
  });

  pi.registerCommand("crowe-review", {
    description: "Review changed files with pi (read-only tools)",
    handler: async (_args, ctx) => {
      ctx.ui.notify("Reviewing changed files...", "info");
      try {
        const diff = execSync("git diff --name-only HEAD", { cwd: PROJECT_ROOT, encoding: "utf-8" });
        const files = diff.trim().split("\n").filter(Boolean);
        if (files.length === 0) {
          ctx.ui.notify("No changed files to review", "info");
          return;
        }
        ctx.ui.notify(`Found ${files.length} changed file(s)`, "info");
        // The actual review happens via the LLM using the read tool on these files
        ctx.ui.showResult("Changed files:\n" + files.join("\n"));
      } catch {
        ctx.ui.notify("Could not detect changed files", "warning");
      }
    },
  });

  // ── Safety: intercept dangerous bash ─────────────────────────────────

  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "bash" && event.input.command) {
      const cmd = event.input.command as string;
      const lower = cmd.toLowerCase();

      // Block rm -rf on project dirs
      if (lower.includes("rm -rf") && !lower.includes("node_modules/") && !lower.includes("__pycache__")) {
        const ok = await ctx.ui.confirm("Dangerous command", `Allow: ${cmd}?`);
        if (!ok) return { block: true, reason: "Blocked by Crowe Logic guard — rm -rf on project dirs requires explicit confirmation" };
      }

      // Warn on sudo
      if (lower.startsWith("sudo ")) {
        const ok = await ctx.ui.confirm("Sudo detected", `Allow sudo execution: ${cmd}?`);
        if (!ok) return { block: true, reason: "Blocked by Crowe Logic guard — sudo requires explicit confirmation" };
      }

      // Block writes to .env
      if ((/\b(env\.example|\.env\.local|\.env)\b/).test(cmd) && (/>|tee|sed.*-i/).test(cmd)) {
        return { block: true, reason: "Blocked by Crowe Logic guard — writing to .env files is prohibited; edit manually" };
      }
    }

    // Block edit/write to .env
    if ((event.toolName === "write" || event.toolName === "edit") && event.input.path) {
      const p = (event.input.path as string).toLowerCase();
      if (p.endsWith(".env") || p.endsWith(".env.local")) {
        return { block: true, reason: "Blocked by Crowe Logic guard — automated writes to .env files are prohibited" };
      }
    }
  });

  // ── Session validators ─────────────────────────────────────────────

  pi.on("session_start", async (_event, ctx) => {
    if (!isInProject()) {
      ctx.ui.notify("📦 Not in a Crowe Logic project (no agents/ + pyproject.toml)", "warning");
      return;
    }

    if (!existsSync(VENV_PY)) {
      ctx.ui.notify("⚠️ .venv not found — run `make install` first", "warning");
    } else {
      // Check Python version
      try {
        const v = execSync(`${VENV_PY} --version`, { encoding: "utf-8" }).trim();
        ctx.ui.notify(`🐍 ${v} | Crowe Logic Foundry`, "info");
      } catch {
        ctx.ui.notify("⚠️ .venv exists but Python is not executable", "error");
      }
    }

    const state = restoreState(ctx);
    if (state?.lastTarget) {
      ctx.ui.notify(`Last build: make ${state.lastTarget} → ${state.lastStatus || "unknown"}`, "info");
    }
  });

  pi.on("session_tree", async (_event, ctx) => {
    const state = restoreState(ctx);
    if (state?.lastTarget) {
      ctx.ui.notify(`Restored build state: make ${state.lastTarget} → ${state.lastStatus || "unknown"}`, "info");
    }
  });
}
