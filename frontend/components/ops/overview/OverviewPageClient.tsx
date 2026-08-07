"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Activity, Cpu, Database, RefreshCcw } from "lucide-react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { opsApi } from "@/lib/ops-api";
import type { SystemStatusPayload } from "@/types/ops";

const OverviewCharts = dynamic(
  () => import("./OverviewCharts").then((mod) => mod.OverviewCharts),
  {
    ssr: false,
    loading: () => <div className="h-[360px] animate-pulse rounded-lg bg-slate-100" />,
  },
);

function KpiCard({ href, icon: Icon, label, value, color, sub }: {
  href: string; icon: React.ElementType; label: string; value: string | number; color: string; sub?: string;
}) {
  return (
    <Link href={href} className="block">
      <Card className="hover:bg-white/[0.04] transition-colors cursor-pointer h-full">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-1.5">
            <Icon className={`h-4 w-4 ${color}`} />
            <span className="text-[11px] text-slate-500 uppercase tracking-wide">{label}</span>
          </div>
          <div className={`text-xl font-bold ${color}`}>{value}</div>
          {sub ? <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div> : null}
        </CardContent>
      </Card>
    </Link>
  );
}

export function OverviewPageClient() {
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<SystemStatusPayload | null>(null);
  const [funnel, setFunnel] = useState<{ steps: { key?: string; label: string; count: number; pct_of_prev?: number; uniqueActors?: number }[] } | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [s, f] = await Promise.all([
        opsApi.systemStatus() as Promise<SystemStatusPayload>,
        opsApi.funnel(30),
      ]);
      setStatus(s);
      setFunnel(f);
    } catch { /* */ }
    setLoading(false);
  };

  useEffect(() => { void load(); }, []);

  if (loading) return (
    <div className="space-y-4 animate-pulse">
      <div className="h-8 w-40 bg-white/5 rounded" />
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-24 bg-white/5 rounded-2xl" />
        ))}
      </div>
    </div>
  );

  const steps = funnel?.steps ?? [];
  const cache = status?.cache;
  const cacheAnalysis = cache?.analysis;
  const td = status?.training_data;
  const features = status?.features;
  const coverage = td?.city_coverage;
  const truthRows = td?.truth_records?.row_count ?? 0;

  const cacheBuckets = cache ? [
    { name: "API", value: cache.api_cache_entries ?? 0 },
    { name: "Forecast", value: cache.open_meteo_forecast_entries ?? 0 },
    { name: "METAR", value: cache.metar_entries ?? 0 },
    { name: "TAF", value: cache.taf_entries ?? 0 },
    { name: "Settlement", value: cache.settlement_entries ?? 0 },
  ].filter((d) => d.value > 0) : [];

  const cachePie = cacheAnalysis ? [
    { name: "Hits", value: cacheAnalysis.cache_hits ?? 0, color: "#22c55e" },
    { name: "Misses", value: cacheAnalysis.cache_misses ?? 0, color: "#f59e0b" },
    { name: "Force Refresh", value: cacheAnalysis.force_refresh_requests ?? 0, color: "#3b82f6" },
  ] : [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Overview</h1>
          <p className="text-xs text-slate-500 mt-1">Real-time system snapshot</p>
        </div>
        <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
          <RefreshCcw className="h-3.5 w-3.5" /> Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
        <KpiCard href="/ops/system" icon={Activity} label="System" value={status?.db?.ok ? "OK" : "FAIL"} color={status?.db?.ok ? "text-emerald-400" : "text-red-400"} />
        <KpiCard href="/ops/training" icon={Cpu} label="Truth Records" value={truthRows} color="text-purple-400" sub={`${coverage?.with_truth_rows ?? 0} Cities`} />
        <KpiCard href="/ops/analytics" icon={Activity} label="30d Visits" value={steps.find((s) => s.key === "landing_view")?.count ?? 0} color="text-cyan-400" sub={`${steps.find((s) => s.key === "enter_terminal")?.count ?? 0} Terminal`} />
        <KpiCard href="/ops/system" icon={Cpu} label="Prob. Engine" value={status?.probability?.engine_mode ?? "—"} color="text-amber-400" />
      </div>

      <OverviewCharts
        steps={steps}
        cacheBuckets={cacheBuckets}
        cachePie={cachePie}
        cacheAnalysis={cacheAnalysis}
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {features && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Feature Flags</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(features).map(([k, v]) => (
                  <Badge key={k} variant={v ? "default" : "secondary"} className="text-[11px]">
                    {k.replace(/_/g, " ")}: {String(v)}
                  </Badge>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {coverage && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">City Model Coverage</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-xl bg-white/5 p-3 text-center">
                  <div className="text-xl font-bold text-cyan-400">{coverage.total_cities ?? 0}</div>
                  <div className="text-[11px] text-slate-500">Total Cities</div>
                </div>
                <div className="rounded-xl bg-white/5 p-3 text-center">
                  <div className="text-xl font-bold text-emerald-400">{coverage.with_truth_rows ?? 0}</div>
                  <div className="text-[11px] text-slate-500">Has Truth</div>
                </div>
                <div className="rounded-xl bg-white/5 p-3 text-center">
                  <div className="text-xl font-bold text-purple-400">{coverage.with_feature_rows ?? 0}</div>
                  <div className="text-[11px] text-slate-500">Has Features</div>
                </div>
                <div className="rounded-xl bg-white/5 p-3 text-center">
                  <div className="text-xl font-bold text-amber-400">{td?.truth_records?.row_count ?? 0}</div>
                  <div className="text-[11px] text-slate-500">Truth Rows</div>
                </div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}