import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const dashboardRoot = resolve(process.cwd(), "../dashboard/public");


test("Research V3 dashboard asset is syntactically valid and wired", () => {
  const script = resolve(dashboardRoot, "research-v3.js");
  execFileSync(process.execPath, ["--check", script], { stdio: "pipe" });

  const html = readFileSync(resolve(dashboardRoot, "index.html"), "utf8");
  assert.match(html, /research-v3\.js/);
  assert.match(html, /PAPER V2 \+ RESEARCH V3/);
});
