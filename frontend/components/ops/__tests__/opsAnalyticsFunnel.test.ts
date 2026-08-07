import fs from "node:fs";
import path from "node:path";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

export function runTests() {
  const projectRoot = process.cwd();
  const opsApi = fs.readFileSync(path.join(projectRoot, "lib", "ops-api.ts"), "utf8");
  const analyticsPage = fs.readFileSync(
    path.join(projectRoot, "components", "ops", "analytics", "AnalyticsPageClient.tsx"),
    "utf8",
  );
  const overviewPage = fs.readFileSync(
    path.join(projectRoot, "components", "ops", "overview", "OverviewPageClient.tsx"),
    "utf8",
  );
  const appAnalytics = fs.readFileSync(path.join(projectRoot, "lib", "app-analytics.ts"), "utf8");
  const analyticsRoute = fs.readFileSync(
    path.join(projectRoot, "app", "api", "analytics", "events", "route.ts"),
    "utf8",
  );
  const authMeRoute = fs.readFileSync(path.join(projectRoot, "app", "api", "auth", "me", "route.ts"), "utf8");

  assert(
    opsApi.includes('"landing_view", "enter_terminal", "login_start", "signup_success", "trial_created", "payment_start", "payment_success"') &&
      opsApi.includes("diagnostics?:") &&
      opsApi.includes("traffic?:") &&
      opsApi.includes("uniqueActors"),
    "ops funnel API client must preserve the full standard funnel and expose diagnostics/traffic dimensions",
  );
  assert(
    analyticsPage.includes("Landing page visits") &&
      analyticsPage.includes("Enter terminal") &&
      analyticsPage.includes("Registration success") &&
      analyticsPage.includes("Auth degraded") &&
      analyticsPage.includes("Source & device") &&
      analyticsPage.includes("paymentSuccess?.count") &&
      !analyticsPage.includes("Total registration") &&
      !analyticsPage.includes("Click advanced features"),
    "ops analytics page must show the real funnel semantics instead of stale index-based labels",
  );
  assert(
    appAnalytics.includes("referrer: document.referrer") &&
      appAnalytics.includes("device_type: getDeviceType()") &&
      analyticsRoute.includes("cf-ipcountry") &&
      analyticsRoute.includes("user_agent"),
    "client analytics events must carry source, country, and device metadata for acquisition analysis",
  );
}
