import { createRemoteJWKSet, jwtVerify } from "jose";

const SECURITY_HEADERS = {
  "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; style-src-attr 'unsafe-inline'; script-src 'self'; script-src-attr 'none'; object-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; upgrade-insecure-requests",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
};

const jwksCache = new Map();

export function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) headers.set(key, value);
  const contentType = response.headers.get("Content-Type") || "";
  const privateContent = contentType.includes("text/html") || contentType.includes("json");
  if (!headers.has("Cache-Control")) {
    headers.set("Cache-Control", privateContent ? "no-store" : "public, max-age=300");
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function getRemoteKeySet(teamDomain) {
  const normalized = teamDomain.replace(/\/$/, "");
  if (!jwksCache.has(normalized)) {
    jwksCache.set(
      normalized,
      createRemoteJWKSet(new URL(`${normalized}/cdn-cgi/access/certs`)),
    );
  }
  return jwksCache.get(normalized);
}

export async function verifyAccessRequest(request, env, dependencies = {}) {
  const teamDomain = String(env.ACCESS_TEAM_DOMAIN || "").replace(/\/$/, "");
  const audience = String(env.ACCESS_POLICY_AUD || "");
  const ownerEmail = String(env.ACCESS_OWNER_EMAIL || "").trim().toLowerCase();
  if (!teamDomain || !audience || !ownerEmail) {
    return { ok: false, detail: "access verification is not configured" };
  }
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return { ok: false, detail: "missing Cloudflare Access assertion" };
  const verify = dependencies.jwtVerify || jwtVerify;
  const keySet = dependencies.keySet || getRemoteKeySet(teamDomain);
  try {
    const { payload } = await verify(token, keySet, {
      issuer: teamDomain,
      audience,
    });
    const email = String(payload.email || "").trim().toLowerCase();
    if (!email || email !== ownerEmail) {
      return { ok: false, detail: "authenticated identity is not the owner" };
    }
    return { ok: true, payload };
  } catch {
    return { ok: false, detail: "invalid Cloudflare Access assertion" };
  }
}

export function isControlRequestAuthorized(request, incoming) {
  const origin = request.headers.get("Origin");
  const contentType = request.headers.get("Content-Type") || "";
  return origin === incoming.origin && contentType.toLowerCase().startsWith("application/json");
}

async function proxyApi(request, env) {
  if (!env.ORIGIN_BASE_URL || !env.ORIGIN_SHARED_SECRET) {
    return Response.json({ detail: "origin is not configured" }, { status: 503 });
  }
  const incoming = new URL(request.url);
  const isControl = incoming.pathname.startsWith("/api/v1/control/");
  if (isControl && !isControlRequestAuthorized(request, incoming)) {
    return Response.json({ detail: "invalid control request" }, { status: 403 });
  }
  const target = new URL(`${incoming.pathname}${incoming.search}`, env.ORIGIN_BASE_URL);
  const headers = new Headers(request.headers);
  headers.delete("cookie");
  headers.delete("authorization");
  headers.delete("origin");
  headers.delete("cf-access-jwt-assertion");
  headers.set("X-Sibyl-Gateway-Secret", env.ORIGIN_SHARED_SECRET);
  if (isControl) {
    if (!env.ADMIN_TOKEN) {
      return Response.json({ detail: "admin controls unavailable" }, { status: 503 });
    }
    headers.set("X-Sibyl-Admin-Token", env.ADMIN_TOKEN);
  }
  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    redirect: "manual",
  });
  const outputHeaders = new Headers(response.headers);
  outputHeaders.delete("set-cookie");
  outputHeaders.set("Cache-Control", "no-store");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: outputHeaders,
  });
}

export default {
  async fetch(request, env) {
    const authentication = await verifyAccessRequest(request, env);
    if (!authentication.ok) {
      return withSecurityHeaders(
        Response.json({ detail: authentication.detail }, { status: 403 }),
      );
    }
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      if (!["GET", "POST"].includes(request.method)) {
        return withSecurityHeaders(new Response("Method not allowed", { status: 405 }));
      }
      return withSecurityHeaders(await proxyApi(request, env));
    }
    return withSecurityHeaders(await env.ASSETS.fetch(request));
  },
};
