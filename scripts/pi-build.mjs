#!/usr/bin/env node
/**
 * Crowe Logic — Pi-Enhanced Build Orchestrator
 *
 * Usage:
 *   node scripts/pi-build.mjs [review|build|ci]
 *
 * Modes:
 *   review  — run pi read-only code review on changed Python files (default)
 *   build   — lint → review → test (gated, fails fast)
 *   ci      — non-interactive JSON output for GitHub Actions
 *
 * Requires: Node >= 20.6, pi-coding-agent >= 0.73
 * Falls back to make-only if pi SDK is unavailable.
 */

import { execSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const PROJECT_ROOT = process.cwd();
const VENV_PY = resolve(PROJECT_ROOT, ".venv/bin/python");
const PY = existsSync(VENV_PY) ? VENV_PY : "python3";
const MODE = process.argv[2] || "review";

let piSdkAvailable = false;

try {
  await import("@mariozechner/pi-coding-agent");
  piSdkAvailable = true;
} catch {
  piSdkAvailable = false;
}

function run(cmd, args = [], opts = {}) {
  return new Promise((res) => {
    const child = spawn(cmd, args, {
      cwd: PROJECT_ROOT,
      stdio: opts.silent ? ["ignore", "pipe", "pipe"] : "inherit",
      env: { ...process.env, ...opts.env },
      timeout: opts.timeout || 300_000,
    });
    let out = "";
    let err = "";
    child.stdout?.on("data", (d) => { out += d; });
    child.stderr?.on("data", (d) => { err += d; });
    child.on("close", (code) => res({ exitCode: code ?? 1, stdout: out, stderr: err }));
    child.on("error", (e) => res({ exitCode: 1, stdout: out, stderr: err + e.message }));
  });
}

async function reviewWithPi(files) {
  if (!piSdkAvailable) {
    console.error("⚠️  pi SDK not available. Skipping AI review.");
    console.error("   Install with: npm install -g @mariozechner/pi-coding-agent");
    return { pass: true, note: "sdk_unavailable" };
  }

  const { createAgentSession } = await import("@mariozechner/pi-coding-agent");
  const { session } = await createAgentSession({
    tools: ["read", "grep", "find", "ls"],
    settingsManager: { get: () => undefined, set: () => {}, getAll: () => ({}) },
  });

  let verdict = "PASS";
  let response = "";

  session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_delta") {
      const delta = event.assistantMessageEvent.delta;
      response += delta;
      if (MODE !== "ci") process.stdout.write(delta);
    }
  });

  const prompt = `
Review these changed files for security, performance, and maintainability issues.
Files: ${files.join(", ")}

Use the read tool to inspect the actual code where needed.
Focus on: SQL injection, path traversal, unsafe eval, hardcoded secrets, N+1 queries,
missing type hints, FastAPI anti-patterns, and untested error paths.

Output a one-sentence verdict (PASS / NEEDS WORK / BLOCKING) followed by prioritized findings.
`;

  await session.prompt(prompt);
  session.end?.();

  const lower = response.toLowerCase();
  if (lower.includes("blocking")) verdict = "BLOCKING";
  else if (lower.includes("needs work")) verdict = "NEEDS WORK";

  return { pass: verdict === "PASS", verdict, response };
}

async function main() {
  const start = Date.now();
  const results = { mode: MODE, stages: [] };

  // ── Lint ─────────────────────────────────────────────────────────────
  if (MODE === "build") {
    console.log("\n━━━ Stage 1: lint ━━━\n");
    const lint = await run(PY, ["-m", "ruff", "check", "."], { silent: true });
    results.stages.push({ stage: "lint", ok: lint.exitCode === 0, stdout: lint.stdout, stderr: lint.stderr });
    if (lint.exitCode !== 0) {
      console.error("❌ Lint failed. Fix before proceeding.");
      if (MODE === "ci") console.log(JSON.stringify(results, null, 2));
      process.exit(1);
    }
    console.log("✅ Lint passed\n");
  }

  // ── Format check ─────────────────────────────────────────────────────
  if (MODE === "build") {
    console.log("━━━ Stage 2: format ━━━\n");
    const fmt = await run(PY, ["-m", "ruff", "format", "--check", "."], { silent: true });
    results.stages.push({ stage: "format", ok: fmt.exitCode === 0, stdout: fmt.stdout, stderr: fmt.stderr });
    if (fmt.exitCode !== 0) {
      console.error("❌ Format check failed. Run: make fmt");
      if (MODE === "ci") console.log(JSON.stringify(results, null, 2));
      process.exit(1);
    }
    console.log("✅ Format clean\n");
  }

  // ── Review ─────────────────────────────────────────────────────────
  if (MODE === "review" || MODE === "build" || MODE === "ci") {
    console.log("━━━ Stage: pi code review ━━━\n");
    let changedFiles = [];
    try {
      const diff = execSync("git diff --name-only HEAD~1 -- '*.py' '*.ts' '*.js' '*.yaml' '*.yml' '*.md' || true", {
        cwd: PROJECT_ROOT, encoding: "utf-8", timeout: 10_000,
      });
      changedFiles = diff.trim().split("\n").filter(Boolean);
    } catch {
      changedFiles = [];
    }

    if (changedFiles.length === 0) {
      console.log("No changed files to review.");
      results.stages.push({ stage: "review", ok: true, note: "no_changes" });
    } else {
      console.log(`Reviewing ${changedFiles.length} file(s)...\n`);
      const review = await reviewWithPi(changedFiles);
      results.stages.push({ stage: "review", ok: review.pass, verdict: review.verdict, note: review.note });
      if (!review.pass) {
        console.error(`\n❌ Review verdict: ${review.verdict}`);
        if (MODE === "ci") console.log(JSON.stringify(results, null, 2));
        process.exit(1);
      }
      console.log(`\n✅ Review verdict: ${review.verdict}\n`);
    }
  }

  // ── Test ─────────────────────────────────────────────────────────────
  if (MODE === "build") {
    console.log("━━━ Stage 4: test ━━━\n");
    const test = await run(PY, ["-m", "pytest", "-q"], { silent: false, timeout: 600_000 });
    results.stages.push({ stage: "test", ok: test.exitCode === 0 });
    if (test.exitCode !== 0) {
      console.error("❌ Tests failed.");
      if (MODE === "ci") console.log(JSON.stringify(results, null, 2));
      process.exit(1);
    }
    console.log("✅ Tests passed\n");
  }

  // ── Done ───────────────────────────────────────────────────────────
  const elapsed = ((Date.now() - start) / 1000).toFixed(1);
  results.elapsedSec = parseFloat(elapsed);
  results.ok = results.stages.every((s) => s.ok);

  if (MODE === "ci") {
    console.log(JSON.stringify(results, null, 2));
  } else {
    console.log(`\n━━━ Build complete in ${elapsed}s ━━━`);
    console.log(results.ok ? "✅ All stages passed" : "❌ Some stages failed");
  }

  process.exit(results.ok ? 0 : 1);
}

main().catch((e) => {
  console.error("Fatal error:", e.message);
  process.exit(1);
});
