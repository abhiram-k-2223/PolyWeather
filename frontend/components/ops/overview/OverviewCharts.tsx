"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CHART_TOOLTIP_STYLE } from "@/lib/chart-utils";

type StepRow = { key?: string; label: string; count: number; pct_of_prev?: number; uniqueActors?: number };
type BucketRow = { name: string; value: number };
type PieRow = { name: string; value: number; color?: string };
type CacheAnalysis = {
  total_requests?: number;
  cache_hits?: number;
  cache_misses?: number;
  force_refresh_requests?: number;
  hit_rate?: number | null;
};

export function OverviewCharts({
  steps,
  cacheBuckets,
  cachePie,
  cacheAnalysis,
}: {
  steps: StepRow[];
  cacheBuckets: BucketRow[];
  cachePie: PieRow[];
  cacheAnalysis?: CacheAnalysis;
}) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="space-y-5">
          {steps.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">30d Conversion Funnel</CardTitle>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={[...steps].reverse().map((s) => ({ name: s.label, count: s.count }))} layout="vertical" margin={{ left: 80, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                    <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} />
                    <YAxis type="category" dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} width={75} />
                    <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                    <Bar dataKey="count" radius={[0, 4, 4, 0]} fill="#06b6d4" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          )}

          {cacheBuckets.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Cache Buckets</CardTitle>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={cacheBuckets} layout="vertical" margin={{ left: 50, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                    <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} />
                    <YAxis type="category" dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} width={45} />
                    <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]} fill="#6366f1" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-5">
          {cachePie.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  Cache Analysis{" "}
                  {cacheAnalysis?.hit_rate != null && (
                    <span className="text-emerald-400 font-normal">{(cacheAnalysis.hit_rate * 100).toFixed(1)}% Hit</span>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-4">
                  <div className="w-36 h-36 shrink-0">
                    <ResponsiveContainer>
                      <PieChart>
                        <Pie data={cachePie} cx="50%" cy="50%" innerRadius={36} outerRadius={58} paddingAngle={2} dataKey="value">
                          {cachePie.map((item, i) => (<Cell key={i} fill={item.color || "#64748b"} />))}
                        </Pie>
                        <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="flex-1 grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <div className="text-slate-500 text-xs">Total Requests</div>
                      <div className="text-white font-bold">{cacheAnalysis?.total_requests ?? 0}</div>
                    </div>
                    <div>
                      <div className="text-slate-500 text-xs">Force Refresh</div>
                      <div className="text-blue-400 font-bold">{cacheAnalysis?.force_refresh_requests ?? 0}</div>
                    </div>
                    <div>
                      <div className="text-slate-500 text-xs">Hits</div>
                      <div className="text-emerald-400 font-bold">{cacheAnalysis?.cache_hits ?? 0}</div>
                    </div>
                    <div>
                      <div className="text-slate-500 text-xs">Misses</div>
                      <div className="text-amber-400 font-bold">{cacheAnalysis?.cache_misses ?? 0}</div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
