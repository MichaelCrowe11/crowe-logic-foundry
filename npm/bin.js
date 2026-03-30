#!/usr/bin/env node
/**
 * Crowe Logic CLI — npm wrapper
 *
 * Delegates to the Python CLI. Requires Python 3.10+ and pip.
 * Usage: npx crowe-logic chat
 */

const { execFileSync, spawn } = require("child_process");

const args = process.argv.slice(2);

// Check if crowe-logic is installed as a Python package
try {
  execFileSync("crowe-logic", ["--version"], { stdio: "pipe" });
} catch {
  console.log("Installing crowe-logic Python package...");
  try {
    execFileSync("pip", ["install", "crowe-logic"], { stdio: "inherit" });
  } catch {
    console.error(
      "\nCrowe Logic requires Python 3.10+. Install via:\n" +
        "  pip install crowe-logic\n"
    );
    process.exit(1);
  }
}

// Delegate to the Python CLI (no shell, no injection risk)
const child = spawn("crowe-logic", args, {
  stdio: "inherit",
});

child.on("exit", (code) => process.exit(code || 0));
