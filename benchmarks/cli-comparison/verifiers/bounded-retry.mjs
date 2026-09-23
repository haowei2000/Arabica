import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = path.resolve(process.argv[2]);
const moduleUrl = `${pathToFileURL(path.join(workspace, "src/retry.js")).href}?verify=1`;

try {
  const { withRetry } = await import(moduleUrl);
  let attempts = 0;
  const value = await withRetry(async () => {
    attempts += 1;
    if (attempts < 3) throw new Error("transient");
    return "ok";
  }, { retries: 2, delayMs: 0 });
  assert.equal(value, "ok");
  assert.equal(attempts, 3);
  attempts = 0;
  await assert.rejects(() => withRetry(async () => {
    attempts += 1;
    throw new Error(`failure-${attempts}`);
  }, { retries: 1 }), /failure-2/);
  assert.equal(attempts, 2);
  await assert.rejects(() => withRetry(async () => "unused", { retries: -1 }), TypeError);
  await assert.rejects(() => withRetry(async () => "unused", { retries: 1.5 }), TypeError);
  process.stdout.write(JSON.stringify({ passed: true, score_bps: 10000, checks: ["eventual-success", "bounded-failure", "validation"] }));
} catch (error) {
  process.stdout.write(JSON.stringify({ passed: false, score_bps: 0, checks: [], error: String(error?.message ?? error) }));
}
