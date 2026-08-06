import assert from "node:assert/strict";
import test from "node:test";

import { isControlRequestAuthorized } from "../src/index.js";

const target = new URL("https://trace.example.com/api/v1/control/pause");

function request(headers) {
  return new Request(target, { method: "POST", headers, body: "{}" });
}

test("accepts same-origin JSON owner control", () => {
  assert.equal(
    isControlRequestAuthorized(
      request({ Origin: target.origin, "Content-Type": "application/json" }),
      target,
    ),
    true,
  );
});

test("rejects cross-origin owner control", () => {
  assert.equal(
    isControlRequestAuthorized(
      request({ Origin: "https://attacker.example", "Content-Type": "application/json" }),
      target,
    ),
    false,
  );
});

test("rejects form-compatible owner control", () => {
  assert.equal(
    isControlRequestAuthorized(
      request({ Origin: target.origin, "Content-Type": "text/plain" }),
      target,
    ),
    false,
  );
});
