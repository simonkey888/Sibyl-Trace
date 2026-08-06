const SECURITY_HEADERS = {
  "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Cross-Origin-Opener-Policy": "same-origin",
};

function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) headers.set(key, value);
  const html = response.headers.get("Content-Type")?.includes("text/html");
  headers.set("Cache-Control", html ? "no-store" : "public, max-age=300");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function proxyApi(request, env) {
  if (!env.ORIGIN_BASE_URL || !env.ORIGIN_SHARED_SECRET) {
    return Response.json({ detail: "origin is not configured" }, { status: 503 });
  }
  const incoming = new URL(request.url);
  const target = new URL(`${incoming.pathname}${incoming.search}`, env.ORIGIN_BASE_URL);
  const headers = new Headers(request.headers);
  headers.delete("cookie");
  headers.delete("authorization");
  headers.set("X-Sibyl-Gateway-Secret", env.ORIGIN_SHARED_SECRET);
  if (incoming.pathname.startsWith("/api/v1/control/")) {
    if (!env.ADMIN_TOKEN) return Response.json({ detail: "admin controls unavailable" }, { status: 503 });
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
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers: outputHeaders });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      if (!["GET", "POST"].includes(request.method)) return new Response("Method not allowed", { status: 405 });
      return withSecurityHeaders(await proxyApi(request, env));
    }
    return withSecurityHeaders(await env.ASSETS.fetch(request));
  },
};
