import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const v4 = readFileSync(new URL("./research-v4.js", import.meta.url), "utf8");

assert.match(css, /:root\s*\{[\s\S]*font-size:\s*16px;/, "root typography must remain 16px");
assert.match(css, /body\s*\{[\s\S]*font-size:\s*15px;/, "body typography must remain readable");
assert.doesNotMatch(css, /(?:font-size\s*:\s*|font\s*:[^;]*\s)(?:[0-7](?:\.\d+)?)px\b/, "dashboard must not regress to 0-7px microtext");
assert.match(css, /@media \(max-width: 700px\)/, "mobile composition must remain explicit");
assert.match(css, /prefers-reduced-motion/, "motion must respect reduced-motion preferences");
assert.match(html, /class="skip-link"/, "keyboard users need a skip link");
assert.match(html, /aria-label="Primary navigation"/, "primary navigation needs an accessible label");
assert.match(html, /research-v4\.js/, "operational V4 evidence must be loaded in the terminal");
assert.match(v4, /research_v4/, "V4 panel must consume the sanitized research_v4 snapshot");
assert.match(v4, /order placement \$\{safety\.order_placement === false \? "disabled"/, "V4 panel must expose the no-order safety state");

console.log("dashboard design contract: PASS");
