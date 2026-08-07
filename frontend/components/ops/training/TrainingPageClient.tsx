"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { RefreshCcw, TrendingUp, TrendingDown, Target, Activity } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buildDebRecentRankingRows } from "@/lib/deb-training-ranking";
import { opsApi } from "@/lib/ops-api";
import type { SystemStatusPayload } from "@/types/ops";
import Link from "next/link";

const TrainingAccuracyCharts = dynamic(
  () => import("./TrainingAccuracyCharts").then((mod) => mod.TrainingAccuracyCharts),
  {
    ssr: false,
    loading: () => <div className="h-[400px] animate-pulse rounded-lg bg-slate-100" />,
  },
);

function StatRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between py-1.5 border-b border-white/5 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-white font-medium">{value}</span>
    </div>
  );
}

function KpiCard({ label, value, sub, icon: Icon, color }: {
  label: string; value: string | number; sub?: string;
  icon: React.ComponentType<{ className?: string }>; color: string;
}) {
  return (
    <Card className="bg-slate-900/60 border-white/5">
      <CardContent className="p-4 flex items-center gap-4">
        <div className={`p-3 rounded-xl ${color}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <div className="text-2xl font-bold text-white">{value}</div>
          <div className="text-xs text-slate-400">{label}</div>
          {sub ? <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div> : null}
        </div>
      </CardContent>
    </Card>
  );
}

interface CityAccuracy {
  city_id: string;
  name: string;
  deb?: {
    hit_rate: number;
    mae: number;
    total_days: number;
    details_str: string;
  } | null;
  deb_recent?: {
    recent_7d?: {
      hit_rate?: number | null;
      samples?: number;
      mae?: number | null;
    };
    recent_14d?: {
      hit_rate?: number | null;
      samples?: number;
      mae?: number | null;
    };
    trust_tier?: string;
    recommendation?: string;
    bias_direction?: string;
    reason?: string;
  } | null;
  mu?: {
    mae: number;
    hit_rate: number;
    brier_score: number | null;
    total_days: number;
    details_str: string;
  } | null;
}

interface DebSummary {
  historical?: {
    avg_hit_rate?: number | null;
    weighted_hit_rate?: number | null;
    avg_mae?: number | null;
    sample_days?: number;
    city_count?: number;
  };
  usable_recent?: {
    window?: string;
    city_count?: number;
    samples?: number;
    hits?: number;
    hit_rate?: number | null;
    avg_mae?: number | null;
    recommendations?: {
      primary?: number;
      supporting?: number;
    };
  };
  recent_7d?: {
    hit_rate?: number | null;
    mae?: number | null;
    samples?: number;
    hits?: number;
  };
  recent_14d?: {
    hit_rate?: number | null;
    mae?: number | null;
    samples?: number;
    hits?: number;
  };
}

function debTrustBadgeClass(tier?: string) {
  if (tier === "high") return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
  if (tier === "medium") return "bg-amber-500/15 text-amber-300 border-amber-500/30";
  if (tier === "low") return "bg-rose-500/15 text-rose-300 border-rose-500/30";
  return "bg-slate-500/15 text-slate-300 border-slate-500/30";
}

function debTrustLabel(tier?: string) {
  if (tier === "high") return "High Confidence";
  if (tier === "medium") return "Medium Confidence";
  if (tier === "low") return "Low Confidence";
  return "Low Sample";
}

function debRecommendationLabel(recommendation?: string) {
  if (recommendation === "primary") return "Primary";
  if (recommendation === "supporting") return "Supporting";
  if (recommendation === "context_only") return "Context Only";
  return "Insufficient";
}

function formatPct(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(0)}%`;
}

export function TrainingPageClient() {
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<SystemStatusPayload | null>(null);
  const [accuracy, setAccuracy] = useState<CityAccuracy[] | null>(null);
  const [debSummary, setDebSummary] = useState<DebSummary | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [s, accData] = await Promise.all([
        opsApi.systemStatus() as Promise<SystemStatusPayload>,
        opsApi.trainingAccuracy().catch(() => ({ accuracy: [] as CityAccuracy[], deb_summary: null })),
      ]);
      setStatus(s);
      setAccuracy((accData as { accuracy: CityAccuracy[] }).accuracy ?? []);
      setDebSummary((accData as { deb_summary?: DebSummary | null }).deb_summary ?? null);
    } catch { /* */ }
    setLoading(false);
  };

  useEffect(() => { void load(); }, []);

  const kpis = useMemo(() => {
    if (!accuracy?.length) return null;
    const debCities = accuracy.filter((c) => c.deb);
    const avgHit = debCities.reduce((s, c) => s + (c.deb?.hit_rate ?? 0), 0) / debCities.length;
    const avgMae = debCities.reduce((s, c) => s + (c.deb?.mae ?? 0), 0) / debCities.length;
    const best = debCities.reduce((a, b) => ((a.deb?.hit_rate ?? 0) > (b.deb?.hit_rate ?? 0) ? a : b));
    const worst = debCities.reduce((a, b) => ((a.deb?.mae ?? 0) > (b.deb?.mae ?? 0) ? a : b));
    return {
      avgHit: debSummary?.historical?.avg_hit_rate ?? avgHit,
      avgMae: debSummary?.historical?.avg_mae ?? avgMae,
      usableRecent: debSummary?.usable_recent,
      recent7Hit: debSummary?.recent_7d?.hit_rate,
      recent14Hit: debSummary?.recent_14d?.hit_rate,
      best,
      worst,
    };
  }, [accuracy, debSummary]);

  const usableWindowLabel = (window?: string) =>
    window === "recent_14d" ? "DEB Recent 14d Hit" : "DEB Recent 7d Hit";

  const debRecentRanked = useMemo(() => buildDebRecentRankingRows(accuracy || []), [accuracy]);
  const debRecentRankIndex = useMemo(
    () => new Map(debRecentRanked.map((row, index) => [row.cityId, index])),
    [debRecentRanked],
  );

  const debChartData = useMemo(() => {
    return debRecentRanked
      .slice(0, 24)
      .map((c) => ({
        name: c.name,
        cityId: c.cityId,
        hitRate: c.hitRate,
        mae: c.mae,
        days: c.samples,
      }));
  }, [debRecentRanked]);

  const muChartData = useMemo(() => {
    if (!accuracy?.length) return [];
    return accuracy
      .filter((c) => c.mu && c.mu.total_days >= 5 && c.mu.brier_score !== null)
      .sort((a, b) => ((a.mu?.brier_score ?? 1) - (b.mu?.brier_score ?? 1)))
      .map((c) => ({
        name: c.name,
        cityId: c.city_id,
        brierScore: Number((c.mu?.brier_score ?? 0).toFixed(4)),
        hitRate: Number((c.mu?.hit_rate ?? 0).toFixed(1)),
        mae: Number((c.mu?.mae ?? 0).toFixed(1)),
        days: c.mu?.total_days ?? 0,
      }));
  }, [accuracy]);

  const sortedAccuracy = useMemo(() => {
    if (!accuracy?.length) return [];
    return [...accuracy].sort((a, b) => {
      const aDebRank = debRecentRankIndex.get(a.city_id) ?? 9999;
      const bDebRank = debRecentRankIndex.get(b.city_id) ?? 9999;
      if (aDebRank !== bDebRank) return aDebRank - bDebRank;
      const aMax = Math.max(a.deb?.hit_rate ?? 0, a.mu?.hit_rate ?? 0);
      const bMax = Math.max(b.deb?.hit_rate ?? 0, b.mu?.hit_rate ?? 0);
      return bMax - aMax;
    });
  }, [accuracy, debRecentRankIndex]);

  if (loading) return <div className="text-slate-400 animate-pulse">Loading...</div>;
  if (!status) return <div className="text-red-400">Load failed</div>;

  const td = status.training_data;
  const truth = td?.truth_records;
  const features = td?.training_features;
  const coverage = td?.city_coverage;
  const modelCities = td?.model_cities;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Training Data</h1>
        <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
          <RefreshCcw className="h-3.5 w-3.5" /> Refresh
        </Button>
      </div>

      {/* Data volume KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card>
          <CardHeader><CardTitle>Truth Records</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            <StatRow label="Rows" value={truth?.row_count ?? "—"} />
            <StatRow label="Cities" value={truth?.cities_count ?? "—"} />
            <StatRow label="Date Range" value={truth?.min_date && truth?.max_date ? `${truth.min_date} ~ ${truth.max_date}` : "—"} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Training Features</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            <StatRow label="Rows" value={features?.row_count ?? "—"} />
            <StatRow label="Cities" value={features?.cities_count ?? "—"} />
            <StatRow label="Date Range" value={features?.min_date && features?.max_date ? `${features.min_date} ~ ${features.max_date}` : "—"} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>City Coverage</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            <StatRow label="Total Cities" value={coverage?.total_cities ?? "—"} />
            <StatRow label="Has Truth" value={coverage?.with_truth_rows ?? "—"} />
            <StatRow label="Has Features" value={coverage?.with_feature_rows ?? "—"} />
          </CardContent>
        </Card>
      </div>

      {/* Accuracy KPI row */}
      {kpis ? (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
          <KpiCard
            icon={Target} color="bg-cyan-500/20 text-cyan-400"
            label={usableWindowLabel(kpis.usableRecent?.window)}
            value={kpis.usableRecent?.hit_rate == null ? "—" : `${kpis.usableRecent.hit_rate.toFixed(1)}%`}
            sub={`Cities: ${kpis.usableRecent?.city_count ?? 0} · Samples: ${kpis.usableRecent?.samples ?? 0} · History: ${kpis.avgHit.toFixed(1)}%`}
          />
          <KpiCard
            icon={Target} color="bg-emerald-500/20 text-emerald-400"
            label="7d Hit" value={kpis.recent7Hit == null ? "—" : `${kpis.recent7Hit.toFixed(1)}%`}
          />
          <KpiCard
            icon={Activity} color="bg-violet-500/20 text-violet-400"
            label="14d Hit" value={kpis.recent14Hit == null ? "—" : `${kpis.recent14Hit.toFixed(1)}%`}
          />
          <KpiCard
            icon={Activity} color="bg-blue-500/20 text-blue-400"
            label="Avg MAE" value={`${kpis.avgMae.toFixed(1)}°`}
          />
          <KpiCard
            icon={TrendingUp} color="bg-emerald-500/20 text-emerald-400"
            label="Best City" value={kpis.best.name}
            sub={`Hit ${kpis.best.deb?.hit_rate.toFixed(0)}% · MAE ${kpis.best.deb?.mae.toFixed(1)}°`}
          />
          <KpiCard
            icon={TrendingDown} color="bg-rose-500/20 text-rose-400"
            label="Worst City" value={kpis.worst.name}
            sub={`MAE ${kpis.worst.deb?.mae.toFixed(1)}° · ${kpis.worst.deb?.total_days}d`}
          />
        </div>
      ) : null}

      <TrainingAccuracyCharts debChartData={debChartData} muChartData={muChartData} />

      {/* City coverage */}
      {modelCities ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card>
            <CardHeader><CardTitle>Strongest Cities</CardTitle></CardHeader>
            <CardContent>
              {modelCities.strongest?.length ? (
                <ul className="space-y-1">
                  {modelCities.strongest.map((c, i) => (
                    <li key={i} className="text-sm text-slate-300">
                      <span className="text-white font-medium">{c.city}</span>
                      <span className="text-slate-500 ml-3">Truth:{c.truth_rows ?? "—"} Features:{c.feature_rows ?? "—"}</span>
                    </li>
                  ))}
                </ul>
              ) : <span className="text-slate-500 text-sm">No Data</span>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Coverage Gaps</CardTitle></CardHeader>
            <CardContent>
              {modelCities.gaps?.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {modelCities.gaps.map((c) => (
                    <Badge key={c} variant="secondary">{c}</Badge>
                  ))}
                </div>
              ) : <span className="text-slate-500 text-sm">No gaps</span>}
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* Detail table */}
      <Card>
        <CardHeader>
          <CardTitle>Model Fusion & Accuracy Details</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-slate-300">
              <thead className="text-xs uppercase bg-slate-800/50 text-slate-400">
                <tr>
                  <th scope="col" className="px-4 py-3">City</th>
                  <th scope="col" className="px-4 py-3 text-center">DEB Strategy</th>
                  <th scope="col" className="px-4 py-3 text-center">7d / 14d</th>
                  <th scope="col" className="px-4 py-3 text-center">DEB Hit</th>
                  <th scope="col" className="px-4 py-3 text-center">DEB MAE</th>
                  <th scope="col" className="px-4 py-3 text-center">DEB Days</th>
                  <th scope="col" className="px-4 py-3 text-center">μ Hit</th>
                  <th scope="col" className="px-4 py-3 text-center">μ MAE</th>
                  <th scope="col" className="px-4 py-3 text-center">Brier</th>
                  <th scope="col" className="px-4 py-3 text-center">μ Days</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {sortedAccuracy.length > 0 ? (
                  sortedAccuracy.map((row) => (
                    <tr key={row.city_id} className="hover:bg-white/5 transition-colors">
                      <td className="px-4 py-3 font-medium text-white capitalize">
                        {row.name}
                        <span className="text-xs text-slate-500 block font-mono">{row.city_id}</span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        {row.deb_recent ? (
                          <span
                            title={row.deb_recent.reason}
                            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${debTrustBadgeClass(row.deb_recent.trust_tier)}`}
                          >
                            {debTrustLabel(row.deb_recent.trust_tier)} · {debRecommendationLabel(row.deb_recent.recommendation)}
                          </span>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center font-mono text-xs text-slate-300">
                        {row.deb_recent ? (
                          <span>
                            {formatPct(row.deb_recent.recent_7d?.hit_rate)}
                            <span className="mx-1 text-slate-600">/</span>
                            {formatPct(row.deb_recent.recent_14d?.hit_rate)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {row.deb ? (
                          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                            row.deb.hit_rate >= 80
                              ? "bg-green-500/15 text-green-400"
                              : row.deb.hit_rate >= 60
                              ? "bg-yellow-500/15 text-yellow-400"
                              : "bg-red-500/15 text-red-400"
                          }`}>
                            {row.deb.hit_rate.toFixed(0)}%
                          </span>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center font-mono">
                        {row.deb ? `${row.deb.mae.toFixed(1)}°` : "—"}
                      </td>
                      <td className="px-4 py-3 text-center text-slate-400">
                        {row.deb ? row.deb.total_days : "—"}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {row.mu ? (
                          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                            row.mu.hit_rate >= 80
                              ? "bg-green-500/15 text-green-400"
                              : row.mu.hit_rate >= 60
                              ? "bg-yellow-500/15 text-yellow-400"
                              : "bg-red-500/15 text-red-400"
                          }`}>
                            {row.mu.hit_rate.toFixed(0)}%
                          </span>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center font-mono">
                        {row.mu ? `${row.mu.mae.toFixed(1)}°` : "—"}
                      </td>
                      <td className="px-4 py-3 text-center font-mono">
                        {row.mu && row.mu.brier_score !== null ? (
                          <span className={`${
                            row.mu.brier_score <= 0.1
                              ? "text-green-400 font-bold"
                              : row.mu.brier_score <= 0.25
                              ? "text-yellow-400"
                              : "text-red-400"
                          }`}>
                            {row.mu.brier_score.toFixed(3)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3 text-center text-slate-400">
                        {row.mu ? row.mu.total_days : "—"}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-slate-500">
                      No accuracy records
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Truth History</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-slate-400 mb-3">Filter by city and date to view historical Truth Records.</p>
          <Link href="/ops/truth-history" className="inline-flex items-center gap-1.5 rounded-lg bg-cyan-500/15 px-3 py-2 text-xs font-bold text-cyan-200 hover:bg-cyan-500/25 transition-colors">
            Open Truth History →
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
