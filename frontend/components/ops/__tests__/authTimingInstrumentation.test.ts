import fs from "node:fs";
import path from "node:path";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

export function runTests() {
  const projectRoot = process.cwd();
  const authMeRouteSource = fs.readFileSync(
    path.join(projectRoot, "app", "api", "auth", "me", "route.ts"),
    "utf8",
  );

  assert(
    authMeRouteSource.includes("authMeNowMs") &&
      authMeRouteSource.includes("const measure = async") &&
      authMeRouteSource.includes("stages.push"),
    "/api/auth/me proxy must centralize timing so every return path can emit instrumentation",
  );
  assert(
    authMeRouteSource.includes('"Server-Timing"') &&
      authMeRouteSource.includes("auth_headers") &&
      authMeRouteSource.includes("backend_fetch") &&
      authMeRouteSource.includes("total"),
    "/api/auth/me proxy must expose stage durations through Server-Timing for HAR inspection",
  );
  assert(
    authMeRouteSource.includes('response.headers.set("Cache-Control", "no-store")') &&
      authMeRouteSource.indexOf('response.headers.set("Cache-Control", "no-store")') >
        authMeRouteSource.indexOf("const response = res.ok"),
    "/api/auth/me proxy must mark every auth profile response no-store so anonymous state cannot be reused after login",
  );
  const timingLogStart = authMeRouteSource.indexOf("console.info");
  const timingLogEnd = authMeRouteSource.indexOf("return applyAuthResponseCookies");
  const timingLogSource =
    timingLogStart >= 0 && timingLogEnd > timingLogStart
      ? authMeRouteSource.slice(timingLogStart, timingLogEnd)
      : "";
  assert(
    timingLogSource.includes("[auth-me-timing]") &&
      timingLogSource.includes("hasAuthorization") &&
      timingLogSource.includes("hasSupabaseCookie") &&
      !timingLogSource.includes("authUserId") &&
      !timingLogSource.includes("authEmail") &&
      !timingLogSource.includes("userId") &&
      !timingLogSource.includes("email"),
    "/api/auth/me proxy timing logs must include request shape but avoid raw user ids or emails",
  );
}