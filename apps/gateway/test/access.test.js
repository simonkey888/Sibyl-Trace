import assert from "node:assert/strict";
import test from "node:test";

import { verifyAccessRequest } from "../src/index.js";

const request = new Request("https://trace.example.com/", {
  headers: { "Cf-Access-Jwt-Assertion": "signed-token" },
});
const env = {
  ACCESS_TEAM_DOMAIN: "https://team.cloudflareaccess.com",
  ACCESS_POLICY_AUD: "audience",
  ACCESS_OWNER_EMAIL: "owner@example.com",
};

test("fails closed when Access verification is not configured", async () => {
  const result = await verifyAccessRequest(request, {});
  assert.equal(result.ok, false);
});

test("accepts a valid owner assertion", async () => {
  const result = await verifyAccessRequest(request, env, {
    keySet: {},
    jwtVerify: async () => ({ payload: { email: "OWNER@example.com" } }),
  });
  assert.equal(result.ok, true);
});

test("rejects a valid token for another identity", async () => {
  const result = await verifyAccessRequest(request, env, {
    keySet: {},
    jwtVerify: async () => ({ payload: { email: "other@example.com" } }),
  });
  assert.equal(result.ok, false);
});

test("rejects an invalid assertion", async () => {
  const result = await verifyAccessRequest(request, env, {
    keySet: {},
    jwtVerify: async () => {
      throw new Error("bad signature");
    },
  });
  assert.equal(result.ok, false);
});
