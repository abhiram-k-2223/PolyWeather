"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, RefreshCcw, ShieldCheck, Database, Cpu, HardDrive, RadioTower } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { opsApi } from "@/lib/ops-api";
import type {
  ObservationCollectorStatusPayload,
  SourceHealthPayload,
  SystemStatusPayload,
  HealthPayload,
} from "@/types/ops";

function sourceStatusTone(status?: string) {
  if (status === "fresh") return "text-emerald-500";
  if (status === "expected_wait") return "text-blue-500";
  if (status === "delayed") return "text-amber-500";
  if (status === "stale" || status === "missing") return "text-red-500";
  return "text-slate-500";
}

function sourceStatusLabel(status?: string) {
  if (status === "fresh") return "OK";
  if (status === "expected_wait") return "Waiting";
  if (status === "delayed") return "Delayed";
  if (status === "stale") return "Stale";
  if (status === "missing") return "Missing";
  return "Unknown";
}

function collectorStatusTone(status?: string) {
  if (status === "ok") return "text-emerald-500";
  if (status === "due") return "text-blue-500";
  if (status === "cooldown") return "text-amber-500";
  if (status === "failed" || status === "never_run") return "text-red-500";
  return "text-slate-500";
}

function collectorStatusLabel(status?: string) {
  if (status === "ok") return "OK";
  if (status === "due") return "Due";
  if (status === "cooldown") return "Cooldown";
  if (status === "failed") return "Failed";
  if (status === "never_run") return "Never Run";
  return "Unknown";
}

function formatAge(ageMin?: number | null) {
  if (ageMin == null) return "—";
  if (ageMin < 60) return `${Math.round(ageMin)}m`;
  return `${(ageMin / 60).toFixed(1)}h`;
}

function formatSeconds(seconds?: number | null) {
  if (seconds == null) return "—";
  if (seconds <= 0) return "Due";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function formatLatency(ms?: number | null) {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTimestamp(value?: string | null) {
  if (!value) return "—";
  return value.replace("T", " ").replace("Z", "").slice(0, 19);
}

function sourceReasonLabel(reason?: string | null) {
  const key = String(reason || "").trim().toLowerCase();
  if (key === "observation_time_missing") return "Missing observation time";
  if (key === "expected_source_not_present_in_cached_detail") return "Expected Source Not in Cached Detail";
  if (key === "past_expected_cadence") return "Past Expected Cadence";
  if (key === "within_expected_cadence") return "Within Expected Cadence";
  if (key === "missing_observation") return "Missing Observation";
  if (key === "no_cached_detail") return "No City Detail Cache";
  return key || "—";
}

function formatOpsValue(value: unknown) {
  if (typeof value === "boolean") {
    return { label: value ? "TRUE" : "FALSE", active: value };
  }
  if (typeof value === "string" || typeof value === "number") {
    return { label: String(value), active: Boolean(value) };
  }
  if (Array.isArray(value)) {
    return { label: `${value.length} items`, active: value.length > 0 };
  }
  if (value && typeof value === "object") {
    const entries = Object.values(value as Record<string, unknown>);
    const enabled = entries.filter(Boolean).length;
    return { label: `${enabled}/${entries.length} Configured`, active: enabled > 0 };
  }
  return { label: "—", active: false };
}

export function SystemPageClient() {
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [status, setStatus] = useState<SystemStatusPayload | null>(null);
  const [sourceHealth, setSourceHealth] = useState<SourceHealthPayload | null>(null);
  const [collectorStatus, setCollectorStatus] = useState<ObservationCollectorStatusPayload | null>(null);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [h, s, sh, cs] = await Promise.all([
        opsApi.health(),
        opsApi.systemStatus() as Promise<SystemStatusPayload>,
        opsApi.sourceHealth(80) as Promise<SourceHealthPayload>,
        opsApi.observationCollectorStatus(200) as Promise<ObservationCollectorStatusPayload>,
      ]);
      setHealth(h);
      setStatus(s);
      setSourceHealth(sh);
      setCollectorStatus(cs);
    } catch (e) {
      setError(String(e).slice(0, 200));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (loading) {
    return <div className="text-slate-400 animate-pulse">Loading...</div>;
  }

  if (error) {
    return <div className="text-red-400">Load failed: {error}</div>;
  }

  const dbOk = status?.db?.ok ?? health?.db?.ok;
  const cacheAnalysis = status?.cache?.analysis;
  const collectorIssues = (collectorStatus?.entries || [])
    .filter((entry) => {
      const state = String(entry.status || "");
      return ["failed", "cooldown", "never_run", "due"].includes(state) || (entry.failure_count ?? 0) > 0;
    })
    .slice(0, 12);
  const sourceIssues = (sourceHealth?.cities || [])
    .flatMap((city) =>
      (city.sources || [])
        .filter((source) => ["delayed", "stale", "missing", "unknown"].includes(String(source.status || "")))
        .map((source) => ({ city: city.city, source })),
    )
    .slice(0, 10);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">System Status</h1>
        <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
          <RefreshCcw className="h-3.5 w-3.5" /> Refresh
        </Button>
      </div>

      {/* Health badges */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card>
          <CardContent className="p-4 flex items-center gap-3">
            <ShieldCheck className={`h-5 w-5 ${health?.status === "ok" ? "text-emerald-400" : "text-red-400"}`} />
            <div>
              <div className="text-xs text-slate-500">Health</div>
              <div className="text-sm font-bold text-white">{health?.status ?? "—"}</div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 flex items-center gap-3">
            <Database className={`h-5 w-5 ${dbOk ? "text-emerald-400" : "text-red-400"}`} />
            <div>
              <div className="text-xs text-slate-500">Database</div>
              <div className="text-sm font-bold text-white">{dbOk ? "OK" : "FAIL"}</div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 flex items-center gap-3">
            <HardDrive className="h-5 w-5 text-cyan-400" />
            <div>
              <div className="text-xs text-slate-500">Storage Mode</div>
              <div className="text-sm font-bold text-white">{status?.state_storage_mode ?? "—"}</div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 flex items-center gap-3">
            <Cpu className="h-5 w-5 text-cyan-400" />
            <div>
              <div className="text-xs text-slate-500">Prob. Engine</div>
              <div className="text-sm font-bold text-white">{status?.probability?.engine_mode ?? "—"}</div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Features & Integrations */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Feature Flags</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              {status?.features
                ? Object.entries(status.features).map(([k, v]) => {
                    const formatted = formatOpsValue(v);
                    return (
                      <div key={k} className="flex justify-between">
                        <span className="text-slate-400">{k}</span>
                        <Badge variant={formatted.active ? "default" : "secondary"}>{formatted.label}</Badge>
                      </div>
                    );
                  })
                : <span className="text-slate-500">No Data</span>}
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Integrations</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              {status?.integrations
                ? Object.entries(status.integrations).map(([k, v]) => {
                    const formatted = formatOpsValue(v);
                    return (
                      <div key={k} className="flex justify-between">
                        <span className="text-slate-400">{k}</span>
                        <Badge variant={formatted.active ? "default" : "secondary"}>{formatted.label}</Badge>
                      </div>
                    );
                  })
                : <span className="text-slate-500">No Data</span>}
            </dl>
          </CardContent>
        </Card>
      </div>

      {/* Cache Analysis */}
      {cacheAnalysis ? (
        <Card>
          <CardHeader>
            <CardTitle>Cache Analysis</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-5">
              <div>
                <div className="text-slate-500">Total Requests</div>
                <div className="text-lg font-bold text-white">{cacheAnalysis.total_requests ?? 0}</div>
              </div>
              <div>
                <div className="text-slate-500">Hits</div>
                <div className="text-lg font-bold text-emerald-400">{cacheAnalysis.cache_hits ?? 0}</div>
              </div>
              <div>
                <div className="text-slate-500">Misses</div>
                <div className="text-lg font-bold text-amber-400">{cacheAnalysis.cache_misses ?? 0}</div>
              </div>
              <div>
                <div className="text-slate-500">Force refresh</div>
                <div className="text-lg font-bold text-blue-500">{cacheAnalysis.force_refresh_requests ?? 0}</div>
              </div>
              <div>
                <div className="text-slate-500">Hit Rate</div>
                <div className="text-lg font-bold text-cyan-400">
                  {cacheAnalysis.hit_rate != null ? `${(cacheAnalysis.hit_rate * 100).toFixed(0)}%` : "—"}
                </div>
              </div>
            </div>
            <div className="mt-3 text-xs text-slate-500">
              Stats reflect the current backend process cache; resets on deploy.
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-cyan-500" />
            Observation collector
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
            {["ok", "due", "cooldown", "failed", "never_run"].map((key) => (
              <div key={key} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <div className="text-[11px] text-slate-500">{collectorStatusLabel(key)}</div>
                <div className={`text-lg font-black ${collectorStatusTone(key)}`}>
                  {collectorStatus?.status_counts?.[key] ?? 0}
                </div>
              </div>
            ))}
          </div>

          {(collectorStatus?.sources || []).length ? (
            <div className="mb-4 grid grid-cols-1 gap-3 text-xs md:grid-cols-2 xl:grid-cols-3">
              {(collectorStatus?.sources || []).map((source) => (
                <div key={source.source} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="font-mono text-sm font-black text-slate-800">{source.source}</span>
                    <span className={`font-bold ${collectorStatusTone(source.worst_status)}`}>
                      {collectorStatusLabel(source.worst_status)}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-slate-600">
                    <span>City {source.city_count ?? 0}</span>
                    <span>Interval {source.min_interval_sec ?? source.interval_sec ?? "—"}s</span>
                    <span>Failed {source.failure_count ?? 0}</span>
                    <span>Cooldown {source.cooldown_count ?? 0}</span>
                    <span>Delayed {formatLatency(source.avg_latency_ms)}</span>
                    <span title={source.last_success_at || ""}>Success {formatTimestamp(source.last_success_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-500">
              No collector state yet; after first write, shows each source/city last run, Failed count, Delayed Cooldown Status.
            </div>
          )}

          {collectorIssues.length ? (
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full min-w-[860px] text-left text-xs">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Source</th>
                    <th className="px-3 py-2">City</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Last Success</th>
                    <th className="px-3 py-2">Failed Count</th>
                    <th className="px-3 py-2">Delayed</th>
                    <th className="px-3 py-2">Next Due</th>
                    <th className="px-3 py-2">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {collectorIssues.map((entry) => (
                    <tr key={`${entry.source}-${entry.city}`} className="border-t border-slate-100">
                      <td className="px-3 py-2 font-mono font-bold text-slate-800">{entry.source}</td>
                      <td className="px-3 py-2 font-mono text-slate-700">{entry.city}</td>
                      <td className={`px-3 py-2 font-bold ${collectorStatusTone(entry.status)}`}>
                        {collectorStatusLabel(entry.status)}
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-600">{formatTimestamp(entry.last_success_at)}</td>
                      <td className="px-3 py-2 font-mono text-slate-600">{entry.failure_count ?? 0}</td>
                      <td className="px-3 py-2 font-mono text-slate-600">{formatLatency(entry.last_latency_ms)}</td>
                      <td className="px-3 py-2 font-mono text-slate-600">{formatSeconds(entry.due_in_sec)}</td>
                      <td className="max-w-[260px] truncate px-3 py-2 text-slate-500" title={entry.last_error || ""}>
                        {entry.last_error || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700">
              <ShieldCheck className="h-4 w-4" />
              No collector failures, cooldowns, or due backlogs.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <RadioTower className="h-4 w-4 text-blue-500" />
            City data source health
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
            {["fresh", "expected_wait", "delayed", "stale", "missing"].map((key) => (
              <div key={key} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <div className="text-[11px] text-slate-500">{sourceStatusLabel(key)}</div>
                <div className={`text-lg font-black ${sourceStatusTone(key)}`}>
                  {sourceHealth?.status_counts?.[key] ?? 0}
                </div>
              </div>
            ))}
          </div>

          {sourceIssues.length ? (
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full min-w-[760px] text-left text-xs">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-3 py-2">City</th>
                    <th className="px-3 py-2">Source</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Delayed</th>
                    <th className="px-3 py-2">Last Obs</th>
                    <th className="px-3 py-2">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {sourceIssues.map(({ city, source }, index) => (
                    <tr key={`${city}-${source.role}-${source.source_code}-${index}`} className="border-t border-slate-100">
                      <td className="px-3 py-2 font-mono font-bold text-slate-800">{city}</td>
                      <td className="px-3 py-2">
                        <div className="font-semibold text-slate-800">{source.source_label || source.source_code}</div>
                        <div className="text-[11px] text-slate-500">{source.role}</div>
                      </td>
                      <td className={`px-3 py-2 font-bold ${sourceStatusTone(source.status)}`}>
                        {sourceStatusLabel(source.status)}
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-600">{formatAge(source.age_min)}</td>
                      <td className="px-3 py-2 font-mono text-slate-600">{source.observed_at || "—"}</td>
                      <td className="px-3 py-2 text-slate-500" title={source.reason || ""}>
                        {sourceReasonLabel(source.reason)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700">
              <ShieldCheck className="h-4 w-4" />
              No stale/delayed sources found for MGM、KNMI、IMS, or airport stations. Disconnected sources (Latency over threshold) are listed here when present.
            </div>
          )}

          {(sourceHealth?.cities || []).some((city) => !city.cache_exists) ? (
            <div className="mt-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-700">
              <AlertTriangle className="h-4 w-4" />
              Some cities lack full/panel cache (cold start or city not yet warmed).
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* DB Path */}
      {status?.db?.db_path ? (
        <Card>
          <CardHeader>
            <CardTitle>DB Path</CardTitle>
          </CardHeader>
          <CardContent>
            <code className="text-xs text-blue-300 bg-black/40 rounded-lg px-3 py-2 block truncate">
              {status.db.db_path}
            </code>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
