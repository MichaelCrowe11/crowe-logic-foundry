/**
 * Crowe Logic — Programmatic API
 *
 * Exposes the CLI as a spawnable child process for Node.js consumers.
 * Usage:
 *   const { run } = require("crowe-logic");
 *   const result = await run("your prompt here");
 */

const { spawn } = require("child_process");

function run(prompt) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    const child = spawn("crowe-logic", ["run", prompt], { stdio: ["ignore", "pipe", "pipe"] });

    child.stdout.on("data", (data) => chunks.push(data));
    child.stderr.on("data", (data) => chunks.push(data));

    child.on("error", (err) => reject(err));
    child.on("exit", (code) => {
      const output = Buffer.concat(chunks).toString();
      if (code === 0) resolve(output);
      else reject(new Error(`crowe-logic exited with code ${code}: ${output}`));
    });
  });
}

module.exports = { run };
