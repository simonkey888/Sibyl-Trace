import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appPath = path.resolve(here, "../../dashboard/public/app.js");
const source = fs.readFileSync(appPath, "utf8");
const start = source.indexOf("function snapshotFreshness(snapshot) {");
const endMarker = "\n}\n\nfunction statusClass";
const end = source.indexOf(endMarker, start);

assert.notEqual(start, -1, "snapshotFreshness function must exist in app.js");
assert.notEqual(end, -1, "snapshotFreshness function boundary must remain detectable");

const freshnessSource = source.slice(start, end + 2);

function snapshotFreshness(snapshot) {
  const context = { snapshot, result: null };
  vm.runInNewContext(`${freshnessSource}\nresult = snapshotFreshness(snapshot);`, context);
  return context.result;
}

test("public snapshot is fresh inside the canonical three-hour TTL", () => {
  const snapshot = {
    snapshot_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    truth_contract: { max_public_snapshot_age_seconds: 10_800 },
  };
  const result = snapshotFreshness(snapshot);
  assert.equal(result.fresh, true);
  assert.equal(result.maxAgeSeconds, 10_800);
  assert.ok(result.ageSeconds >= 3599 && result.ageSeconds <= 3601);
});

test("public snapshot fails stale after the canonical TTL", () => {
  const snapshot = {
    snapshot_at: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString(),
    truth_contract: { max_public_snapshot_age_seconds: 10_800 },
  };
  const result = snapshotFreshness(snapshot);
  assert.equal(result.fresh, false);
  assert.ok(result.ageSeconds >= 14_399);
});

test("invalid snapshot timestamp fails closed instead of appearing fresh", () => {
  const result = snapshotFreshness({
    snapshot_at: "not-a-date",
    truth_contract: { max_public_snapshot_age_seconds: 10_800 },
  });
  assert.equal(result.fresh, false);
  assert.equal(result.ageSeconds, null);
  assert.equal(result.maxAgeSeconds, 10_800);
});

test("truth-contract TTL overrides the dashboard fallback", () => {
  const snapshot = {
    snapshot_at: new Date(Date.now() - 120 * 1000).toISOString(),
    truth_contract: { max_public_snapshot_age_seconds: 60 },
  };
  const result = snapshotFreshness(snapshot);
  assert.equal(result.fresh, false);
  assert.equal(result.maxAgeSeconds, 60);
});
