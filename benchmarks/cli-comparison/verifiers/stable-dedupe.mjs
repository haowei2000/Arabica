import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import path from "node:path";

const workspace = process.argv[2];
const modulePath = path.join(workspace, "src", "unique.js");
const module = await import(`${pathToFileURL(modulePath).href}?v=${Date.now()}`);
assert.deepEqual(Object.keys(module).sort(), ["uniqueBy"]);

const input = [
  { id: "false-first", key: false },
  { id: "zero-first", key: 0 },
  { id: "empty-first", key: "" },
  { id: "nan-first", key: Number.NaN },
  { id: "false-second", key: false },
  { id: "nan-second", key: Number.NaN },
  { id: "string-zero", key: "0" },
];
const before = input.slice();
let calls = 0;
const result = module.uniqueBy(input, (item) => {
  calls += 1;
  return item.key;
});
assert.equal(calls, input.length);
assert.deepEqual(
  result.map((item) => item.id),
  ["false-first", "zero-first", "empty-first", "nan-first", "string-zero"],
);
assert.notEqual(result, input);
assert.deepEqual(input, before);

console.log(JSON.stringify({
  passed: true,
  score_bps: 10000,
  checks: ["stable-order", "same-value-zero", "single-key-call", "no-mutation"],
}));
