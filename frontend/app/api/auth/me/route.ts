import { NextRequest, NextResponse } from "next/server";
import {
  applyAuthResponseCookies,
  buildBackendRequestHeaders,
} from "@/lib/backend-auth";
import { buildUpstreamErrorResponse } from "@/lib/api-proxy";
import { getLocalDevAuthPayload, isLocalFullAccessHost } from "@/lib/local-dev-access";
import { hasSupabaseSessionCookieValues } from "@/lib/supabase/server";

const API_BASE = process.env.POLYWEATHER_API_BASE_URL;

function authMeNowMs() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function hasRequestSupabaseSessionCookie(req: NextRequest) {
  return hasSupabaseSessionCookieValues(
    req.cookies.getAll().map((item) => ({
      name: item.name,
      value: item.value,
    })),
  );
}

export async function GET(req: NextRequest) {
  const startedAt = authMeNowMs();
  const stages: { durationMs: number; name: string }[] = [];
  const measure = async <T,>(name: string, action: () => Promise<T>): Promise<T> => {
    const stageStartedAt = authMeNowMs();
    try {
      return await action();
    } finally {
      stages.push({
        durationMs: Math.round((authMeNowMs() - stageStartedAt) * 10) / 10,
        name,
      });
    }
  };

  const requestHost =
    req.headers.get("x-forwarded-host") || req.headers.get("host") || req.nextUrl.host;
  if (
    isLocalFullAccessHost(requestHost) ||
    isLocalFullAccessHost(req.nextUrl.hostname)
  ) {
    return NextResponse.json(getLocalDevAuthPayload(), {
      headers: { "Cache-Control": "no-store" },
    });
  }

  if (!API_BASE) {
    return NextResponse.json(
      { error: "POLYWEATHER_API_BASE_URL is not configured" },
      { status: 500 },
    );
  }

  try {
    const backendAuth = await measure("auth_headers", () => buildBackendRequestHeaders(req));
    const url = new URL(`${API_BASE.replace(/\/+$/, "")}/api/auth/me`);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 6000);
    let res: Response;
    try {
      res = await measure("backend_fetch", async () =>
        await fetch(url.toString(), {
          headers: backendAuth.headers,
          cache: "no-store",
          signal: controller.signal,
        }),
      );
    } finally {
      clearTimeout(timeoutId);
    }

    const backendServerTiming = res.headers.get("server-timing") || "";
    const response = res.ok
      ? NextResponse.json(await measure("backend_read", () => res.json()))
      : buildUpstreamErrorResponse(res.status, await measure("backend_read", () => res.text()));

    const totalMs = Math.round((authMeNowMs() - startedAt) * 10) / 10;
    const ownServerTiming = [...stages, { durationMs: totalMs, name: "total" }]
      .map(({ durationMs, name }) => {
        const safeName = name.replace(/[^A-Za-z0-9_-]/g, "_");
        return `${safeName};dur=${Math.max(0, durationMs).toFixed(1)}`;
      })
      .join(", ");
    response.headers.set("Cache-Control", "no-store");
    response.headers.set(
      "Server-Timing",
      backendServerTiming
        ? `${ownServerTiming}, ${backendServerTiming}`
        : ownServerTiming,
    );
    console.info(
      "[auth-me-timing]",
      JSON.stringify({
        backendServerTiming: backendServerTiming || undefined,
        hasAuthorization: Boolean(req.headers.get("authorization")),
        hasSupabaseCookie: hasRequestSupabaseSessionCookie(req),
        stagesMs: Object.fromEntries(stages.map((stage) => [stage.name, stage.durationMs])),
        status: response.status,
        totalMs,
      }),
    );
    return applyAuthResponseCookies(response, backendAuth.response);
  } catch (error) {
    return NextResponse.json(
      {
        authenticated: false,
        error: String(error),
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  }
}
