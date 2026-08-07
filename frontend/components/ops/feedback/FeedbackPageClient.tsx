"use client";

import { useEffect, useMemo, useState } from "react";
import { Bug, CheckCircle2, MessageSquare, RefreshCcw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { opsApi } from "@/lib/ops-api";
import type { UserFeedbackEntry, UserFeedbackPayload } from "@/types/ops";

const STATUS_OPTIONS = [
  { key: "", label: "All" },
  { key: "open", label: "Open" },
  { key: "triaged", label: "Triaged" },
  { key: "investigating", label: "Investigating" },
  { key: "resolved", label: "Resolved" },
  { key: "closed", label: "Closed" },
] as const;

const STATUS_UPDATE_OPTIONS = STATUS_OPTIONS.filter((item) => item.key);

const REWARD_GUIDELINES = [
  { points: "0", title: "Invalid / Duplicate", detail: "Duplicate, cannot reproduce, not an issue" },
  { points: "100", title: "Minor", detail: "Copy, UX, minor notice" },
  { points: "300", title: "Reproducible Bug", detail: "Load failure, operation error, local impact" },
  { points: "500", title: "Data Issue", detail: "City data, charts, key variable anomalies" },
  { points: "1000", title: "High Impact", detail: "Payment, account, subscription, core terminal" },
  { points: "1500", title: "Critical", detail: "Widespread outage or severe loss, use sparingly" },
] as const;

function compactDate(value?: string) {
  if (!value) return "—";
  return value.slice(0, 19).replace("T", " ");
}

function categoryLabel(value?: string) {
  const key = String(value || "").toLowerCase();
  if (key === "bug") return "Bug";
  if (key === "data") return "Data";
  if (key === "idea") return "Suggestion";
  if (key === "payment") return "Payment";
  if (key === "account") return "Account";
  return "Other";
}

function statusLabel(value?: string) {
  const key = String(value || "open").toLowerCase();
  if (key === "open") return "Open";
  if (key === "triaged") return "Triaged";
  if (key === "investigating") return "Investigating";
  if (key === "resolved") return "Resolved";
  if (key === "closed") return "Closed";
  return key;
}

function statusTone(value?: string) {
  const key = String(value || "open").toLowerCase();
  if (key === "open") return "border-red-200 bg-red-50 text-red-700";
  if (key === "triaged") return "border-amber-200 bg-amber-50 text-amber-700";
  if (key === "investigating") return "border-blue-200 bg-blue-50 text-blue-700";
  if (key === "resolved") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  return "border-slate-200 bg-slate-50 text-slate-600";
}

function contextSummary(context?: Record<string, unknown>) {
  if (!context) return "—";
  const city = String(context.city || context.display_city || "").trim();
  const slot = context.slot_index != null ? `slot ${context.slot_index}` : "";
  const source = String(context.source || "").trim();
  const pieces = [city, slot, source].filter(Boolean);
  return pieces.length ? pieces.join(" · ") : "terminal";
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function finiteNumber(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatSeconds(value: unknown) {
  const number = finiteNumber(value);
  return number === null ? null : `${number}s`;
}

function latencyTone(value: unknown) {
  const number = finiteNumber(value);
  if (number === null) return "border-slate-200 bg-slate-50 text-slate-600";
  if (number >= 300) return "border-red-200 bg-red-50 text-red-700";
  if (number >= 60) return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

function feedbackFreshnessBadges(context?: Record<string, unknown>) {
  const freshness = objectRecord(context?.freshness);
  const detailBatchDiagnostics = objectRecord(context?.detail_batch_diagnostics);
  const badges: Array<{ key: string; label: string; tone: string }> = [];

  const livePath = String(freshness?.live_path || context?.live_path || "").trim();
  const liveAge = formatSeconds(freshness?.live_age_sec ?? context?.live_age_sec);
  if (livePath) {
    badges.push({
      key: "live",
      label: liveAge ? `Live ${livePath} ${liveAge}` : `Live ${livePath}`,
      tone: latencyTone(freshness?.live_age_sec ?? context?.live_age_sec),
    });
  }

  const detailStatus = String(freshness?.detail_status || context?.detail_status || "").trim();
  const detailSource = String(freshness?.detail_source || context?.detail_source || "").trim();
  if (detailStatus) {
    badges.push({
      key: "detail",
      label: detailSource ? `Detail ${detailStatus}/${detailSource}` : `Detail ${detailStatus}`,
      tone: ["degraded", "stale_cache"].includes(detailStatus)
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-slate-200 bg-slate-50 text-slate-600",
    });
  }

  const sseServerToClientLatency = formatSeconds(freshness?.sse_server_to_client_latency_sec);
  if (sseServerToClientLatency) {
    badges.push({
      key: "sse-server",
      label: `SSE ${sseServerToClientLatency}`,
      tone: latencyTone(freshness?.sse_server_to_client_latency_sec),
    });
  }

  const sseCollectorToClientLatency = formatSeconds(freshness?.sse_collector_to_client_latency_sec);
  if (sseCollectorToClientLatency) {
    badges.push({
      key: "sse-collector",
      label: `Collect-to-Client ${sseCollectorToClientLatency}`,
      tone: latencyTone(freshness?.sse_collector_to_client_latency_sec),
    });
  }

  const sseSourceToCollectorLatency = formatSeconds(freshness?.sse_source_to_collector_latency_sec);
  if (sseSourceToCollectorLatency) {
    badges.push({
      key: "sse-source",
      label: `Source-to-Collector ${sseSourceToCollectorLatency}`,
      tone: latencyTone(freshness?.sse_source_to_collector_latency_sec),
    });
  }

  const partialReason = String(detailBatchDiagnostics?.partial_reason || "").trim();
  const responseSource = String(detailBatchDiagnostics?.response_source || "").trim();
  if (partialReason || responseSource) {
    badges.push({
      key: "batch",
      label: responseSource
        ? `Batch ${partialReason || "ok"} · ${responseSource}`
        : `Batch ${partialReason}`,
      tone: partialReason
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-slate-200 bg-slate-50 text-slate-600",
    });
  }

  const missingCount = finiteNumber(detailBatchDiagnostics?.missing_count);
  const errorCount = finiteNumber(detailBatchDiagnostics?.error_count);
  if (missingCount || errorCount) {
    badges.push({
      key: "batch-counts",
      label: `Miss ${missingCount || 0} · Error ${errorCount || 0}`,
      tone: "border-red-200 bg-red-50 text-red-700",
    });
  }

  return badges;
}

export function FeedbackPageClient() {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");
  const [payload, setPayload] = useState<UserFeedbackPayload | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const data = (await opsApi.feedback(120, filter)) as UserFeedbackPayload;
      setPayload(data);
    } catch (err) {
      setLoadError(String(err).slice(0, 220));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [filter]);

  const rows = payload?.feedback || [];
  const counts = payload?.status_counts || {};
  const openCount = Number(counts.open || 0);
  const activeCount = Number(counts.open || 0) + Number(counts.triaged || 0) + Number(counts.investigating || 0);

  const categoryCounts = useMemo(() => {
    const acc: Record<string, number> = {};
    rows.forEach((row) => {
      const key = String(row.category || "other");
      acc[key] = (acc[key] || 0) + 1;
    });
    return acc;
  }, [rows]);

  const changeStatus = async (row: UserFeedbackEntry, next: string) => {
    const current = String(row.status || "open").toLowerCase();
    if (!next || next === current) return;
    setUpdatingId(row.id);
    try {
      await opsApi.updateFeedbackStatus(row.id, next);
      await load();
    } finally {
      setUpdatingId(null);
    }
  };

  if (loading && !payload) {
    return <div className="text-slate-400 animate-pulse">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">User Feedback</h1>
        <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
          <RefreshCcw className="h-3.5 w-3.5" /> Refresh
        </Button>
      </div>

      {loadError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Load failed: {loadError}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <Bug className="h-5 w-5 text-red-500" />
            <div>
              <div className="text-xs text-slate-500">Open</div>
              <div className="text-2xl font-black text-slate-950">{openCount}</div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <MessageSquare className="h-5 w-5 text-blue-500" />
            <div>
              <div className="text-xs text-slate-500">Investigating</div>
              <div className="text-2xl font-black text-slate-950">{activeCount}</div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
            <div>
              <div className="text-xs text-slate-500">Resolved</div>
              <div className="text-2xl font-black text-slate-950">{Number(counts.resolved || 0)}</div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-xs text-slate-500">Current List</div>
            <div className="mt-1 text-2xl font-black text-slate-950">{rows.length}</div>
            <div className="mt-1 text-xs text-slate-500">
              Bug {categoryCounts.bug || 0} · Data {categoryCounts.data || 0} · Suggestion {categoryCounts.idea || 0}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>Points reward standard</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-6">
            {REWARD_GUIDELINES.map((item) => (
              <div
                key={item.points}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
              >
                <div className="font-mono text-sm font-black text-blue-700">
                  {item.points} pts
                </div>
                <div className="mt-1 text-xs font-bold text-slate-900">
                  {item.title}
                </div>
                <div className="mt-1 text-[11px] leading-4 text-slate-500">
                  {item.detail}
                </div>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Confirm validity first; mark non-reproducible as Triaged/Investigating. Rewards auto-record to the User account page.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Inbox</CardTitle>
          <div className="flex flex-wrap gap-1.5">
            {STATUS_OPTIONS.map((item) => (
              <button
                key={item.key || "all"}
                type="button"
                onClick={() => setFilter(item.key)}
                className={
                  "rounded border px-2.5 py-1 text-xs font-bold transition " +
                  (filter === item.key
                    ? "border-blue-300 bg-blue-50 text-blue-700"
                    : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50")
                }
              >
                {item.label}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {rows.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
              No feedback yet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-slate-500">
                    <th className="py-2 pr-4 font-bold">Status</th>
                    <th className="py-2 pr-4 font-bold">Type</th>
                    <th className="py-2 pr-4 font-bold">Message</th>
                    <th className="py-2 pr-4 font-bold">Context</th>
                    <th className="py-2 pr-4 font-bold">User</th>
                    <th className="py-2 pr-4 font-bold">Time</th>
                    <th className="py-2 pr-4 font-bold">Reward</th>
                    <th className="py-2 pr-4 font-bold">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const rewardPoints = Number(row.reward_points || 0);
                    const rewardStatus = String(row.reward_status || "").toLowerCase();
                    const hasReward = rewardStatus === "granted" && rewardPoints > 0;
                    const freshnessBadges = feedbackFreshnessBadges(row.context);
                    return (
                      <tr key={row.id} className="border-b border-slate-100 align-top">
                        <td className="py-3 pr-4">
                          <span className={`inline-flex rounded-md border px-2 py-1 text-xs font-bold ${statusTone(row.status)}`}>
                            {statusLabel(row.status)}
                          </span>
                        </td>
                        <td className="py-3 pr-4 text-slate-500">{categoryLabel(row.category)}</td>
                        <td className="max-w-xl py-3 pr-4">
                          <div className="font-semibold leading-5 text-slate-900">{row.message || "—"}</div>
                          {row.contact && <div className="mt-1 text-xs text-slate-500">Contact: {row.contact}</div>}
                        </td>
                        <td className="py-3 pr-4">
                          <div className="font-mono text-xs text-blue-700">{contextSummary(row.context)}</div>
                          {Boolean(row.context?.detail_error) && (
                            <div className="mt-1 max-w-xs text-xs text-amber-700">
                              {String(row.context?.detail_error || "").slice(0, 120)}
                            </div>
                          )}
                          {freshnessBadges.length > 0 && (
                            <div className="mt-2 flex max-w-xs flex-wrap gap-1.5">
                              {freshnessBadges.map((item) => (
                                <span
                                  key={item.key}
                                  className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${item.tone}`}
                                >
                                  {item.label}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td className="py-3 pr-4 font-mono text-xs text-slate-500">
                          {row.user_email || row.user_id || "—"}
                        </td>
                        <td className="whitespace-nowrap py-3 pr-4 text-xs text-slate-500">{compactDate(row.created_at)}</td>
                        <td className="min-w-[170px] py-3 pr-4">
                          {hasReward ? (
                            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs">
                              <div className="font-black text-emerald-700">
                                Granted +{rewardPoints.toLocaleString()} pts
                              </div>
                            </div>
                          ) : (
                            <span className="text-xs text-slate-400">—</span>
                          )}
                        </td>
                        <td className="py-3 pr-4">
                          <select
                            value={String(row.status || "open").toLowerCase()}
                            onChange={(event) => changeStatus(row, event.target.value)}
                            disabled={updatingId === row.id}
                            className="h-8 min-w-[108px] rounded border border-slate-200 bg-white px-2 text-xs font-bold text-slate-600 outline-none transition hover:bg-slate-50 focus:border-blue-300 focus:ring-2 focus:ring-blue-100 disabled:cursor-wait disabled:opacity-60"
                            aria-label="Change Status"
                          >
                            {STATUS_UPDATE_OPTIONS.map((item) => (
                              <option key={item.key} value={item.key}>
                                {item.label}
                              </option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
