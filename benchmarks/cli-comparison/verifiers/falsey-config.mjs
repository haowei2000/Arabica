import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = path.resolve(process.argv[2]);
const moduleUrl = `${pathToFileURL(path.join(workspace, "src/config.js")).href}?verify=1`;

try {
  const { resolveConfig } = await import(moduleUrl);
  const defaults = { enabled: true, retries: 3, label: "default" };
  assert.deepEqual(resolveConfig(defaults, { enabled: false, retries: 0, label: "" }), {
    enabled: false,
    retries: 0,
    label: "",
  });
  assert.deepEqual(resolveConfig(defaults, {}), defaults);
  assert.deepEqual(resolveConfig(defaults, { enabled: undefined }), defaults);
  process.stdout.write(JSON.stringify({ passed: true, score_bps: 10000, checks: ["falsey-values", "missing-values", "api-shape"] }));
} catch (error) {
  process.stdout.write(JSON.stringify({ passed: false, score_bps: 0, checks: [], error: String(error?.message ?? error) }));
}
