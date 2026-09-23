import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import path from "node:path";

const workspace = process.argv[2];
const modulePath = path.join(workspace, "src", "index.js");
const module = await import(`${pathToFileURL(modulePath).href}?v=${Date.now()}`);
assert.deepEqual(Object.keys(module).sort(), ["resolveConfig", "resolveConfigBatch"]);

const defaults = { enabled: true, retries: 2, label: "base" };
const overrides = [
  { enabled: false },
  { retries: 0, label: "" },
  { label: undefined },
];
const defaultsBefore = structuredClone(defaults);
const overridesBefore = overrides.map((entry) => ({ ...entry }));
const resolved = module.resolveConfigBatch(defaults, overrides);
assert.deepEqual(resolved, [
  { enabled: false, retries: 2, label: "base" },
  { enabled: true, retries: 0, label: "" },
  { enabled: true, retries: 2, label: undefined },
]);
assert.equal(new Set(resolved).size, resolved.length);
assert.deepEqual(defaults, defaultsBefore);
assert.deepEqual(overrides, overridesBefore);
assert.throws(() => module.resolveConfigBatch(defaults, null), TypeError);
assert.deepEqual(module.resolveConfig(defaults, { retries: 0 }), {
  enabled: true,
  retries: 0,
  label: "base",
});

console.log(JSON.stringify({
  passed: true,
  score_bps: 10000,
  checks: ["batch-values", "independent-results", "input-immutability", "validation", "api-shape"],
}));
