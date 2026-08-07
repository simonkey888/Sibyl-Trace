import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../../dashboard/public/index.html", import.meta.url), "utf8");
const app = fs.readFileSync(new URL("../../dashboard/public/app.js", import.meta.url), "utf8");


test("dashboard presents PAPER V5 as truthful canonical cohort", () => {
  assert.match(html, /CANONICAL PAPER V5/);
  assert.match(html, /PAPER V5 TRUTH LEDGER/);
  assert.match(html, /WIN \/ LOSS/);
  assert.match(html, /NO_FILL/);
  assert.match(html, /LEGACY V2/);
  assert.match(html, /Fill \/ effective/);
  assert.match(html, /Fee/);
  assert.match(html, /Result/);
});


test("dashboard selects PASS paper_v5 before legacy trial performance", () => {
  assert.match(app, /snapshot\.paper_v5\?\.status === "PASS"/);
  assert.match(app, /const canonical = v5 \|\| trial/);
  assert.match(app, /midpoint_fills === false/);
  assert.match(app, /V5 TRUTH ONLINE/);
  assert.match(app, /LEGACY ONLY/);
  assert.match(html, /resolved BUY entries/i);
});


test("truth ledger distinguishes execution status from resolved result", () => {
  assert.match(app, /function resultText\(order\)/);
  assert.match(app, /UNRESOLVED/);
  assert.match(app, /resolution_status/);
  assert.match(app, /fee_usd/);
  assert.match(app, /fill_fraction/);
  assert.match(app, /effective_price/);
});
