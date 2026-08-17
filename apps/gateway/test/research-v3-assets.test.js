import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const dashboardRoot = resolve(process.cwd(), "../dashboard/public");


test("Research dashboard assets are syntactically valid and wired", () => {
  for (const asset of ["research-v3.js", "research-v4.js"]) {
    execFileSync(process.execPath, ["--check", resolve(dashboardRoot, asset)], { stdio: "pipe" });
  }

  const html = readFileSync(resolve(dashboardRoot, "index.html"), "utf8");
  assert.match(html, /research-v3\.js/);
  assert.match(html, /research-v4\.js/);
  assert.match(html, /PAPER V5 R4\.5 \+ LEGACY EVIDENCE/);
  assert.match(html, /External trader claims are reconstructed from public evidence/);
});


test("Dashboard design contract preserves legibility and accessibility", () => {
  const html = readFileSync(resolve(dashboardRoot, "index.html"), "utf8");
  const css = readFileSync(resolve(dashboardRoot, "styles.css"), "utf8");
  const v4 = readFileSync(resolve(dashboardRoot, "research-v4.js"), "utf8");

  assert.match(css, /:root\s*\{[\s\S]*font-size:\s*16px;/);
  assert.match(css, /body\s*\{[\s\S]*font-size:\s*15px;/);
  assert.doesNotMatch(css, /(?:font-size\s*:\s*|font\s*:[^;]*\s)(?:[0-7](?:\.\d+)?)px\b/);
  assert.match(css, /@media \(max-width: 700px\)/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(html, /class="skip-link"/);
  assert.match(html, /aria-label="Primary navigation"/);
  assert.match(v4, /research_v4/);
  assert.match(v4, /order placement \$\{safety\.order_placement === false \? "disabled"/);
});
