"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { RefreshCcw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { opsApi } from "@/lib/ops-api";

const AnalyticsFunnelChart = dynamic(
  () => import("./AnalyticsFunnelChart").then((mod) => mod.AnalyticsFunnelChart),
  {
    ssr: false,
    loading: () => <div className="h-[360px] animate-pulse rounded-lg bg-slate-100" />,
  },
);

type FunnelStep = {
  key: string;
  label: string;
  count: number;
  uniqueActors: number;
  pct_of_prev?: number;
};
type TopItem = { name: string; count: number };
type AuthDiagnostic = {
  total?: number;
  unique_actors?: number;
  by_reason?: TopItem[];
};

export function AnalyticsPageClient() {
  const [loading, setLoading] = useState(true);
  const [funnel, setFunnel] = useState<FunnelStep[]>([]);
  const [diagnostics, setDiagnostics] = useState<Record<string, AuthDiagnostic>>({});
  const [rates, setRates] = useState<Record<string, number> | null>(null);
  const [traffic, setTraffic] = useState<Record<string, TopItem[]>>({});
  const [days, setDays] = useState(30);

  const load = async () => {
    setLoading(true);
    try {
      const data = await opsApi.funnel(days);
      setFunnel(data.steps);
      setDiagnostics(data.diagnostics ?? {});
      setRates(data.rates ?? null);
      setTraffic(data.traffic ?? {});
    } catch { /* */ }
    setLoading(false);
  };

  useEffect(() => { void load(); }, [days]);

  const chartData = [...funnel].reverse().map((s) => ({
    name: s.label,
    count: s.count,
    pct: s.pct_of_prev,
  }));

  const stepByKey = Object.fromEntries(funnel.map((step) => [step.key, step]));
  const degradedAuth = diagnostics.degraded_auth_profile ?? {};
  const landingView = stepByKey.landing_view;
  const terminalEntry = stepByKey.enter_terminal;
  const signupSuccess = stepByKey.signup_success;
  const trialCreated = stepByKey.trial_created;
  const paymentStart = stepByKey.payment_start;
  const paymentSuccess = stepByKey.payment_success;
  const overallRate =
    landingView?.uniqueActors && paymentSuccess?.uniqueActors
      ? ((paymentSuccess.uniqueActors / landingView.uniqueActors) * 100).toFixed(1)
      : "—";

  if (loading) return <div className="text-slate-400 animate-pulse">Loading...</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Conversion Analytics</h1>
        <div className="flex gap-2">
          {[7, 14, 30].map((d) => (
            <Button
              key={d}
              variant={days === d ? "default" : "outline"}
              size="sm"
              onClick={() => setDays(d)}
            >
              {d}d
            </Button>
          ))}
          <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
            <RefreshCcw className="h-3.5 w-3.5" /> Refresh
          </Button>
        </div>
      </div>

      {funnel.length > 0 && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-slate-500">Landing page visits</div>
              <div className="text-xl font-bold text-white">{landingView?.count ?? 0}</div>
              <div className="mt-0.5 text-xs text-slate-500">Unique {landingView?.uniqueActors ?? 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-slate-500">Enter terminal</div>
              <div className="text-xl font-bold text-cyan-400">{terminalEntry?.count ?? 0}</div>
              <div className="mt-0.5 text-xs text-slate-500">Unique {terminalEntry?.uniqueActors ?? 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-slate-500">Registration success</div>
              <div className="text-xl font-bold text-blue-500">{signupSuccess?.count ?? 0}</div>
              <div className="mt-0.5 text-xs text-slate-500">Trial {trialCreated?.count ?? 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-slate-500">Payment Started</div>
              <div className="text-xl font-bold text-amber-400">{paymentStart?.count ?? 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-slate-500">Payment Success</div>
              <div className="text-xl font-bold text-emerald-400">{paymentSuccess?.count ?? 0}</div>
              <div className="mt-0.5 text-xs text-slate-500">
                Overall Conversion {overallRate === "—" ? "—" : `${overallRate}%`}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-slate-500">Auth degraded</div>
              <div className="text-xl font-bold text-rose-500">{degradedAuth.total ?? 0}</div>
              <div className="mt-0.5 text-xs text-slate-500">Unique {degradedAuth.unique_actors ?? 0}</div>
            </CardContent>
          </Card>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.6fr)]">
        <Card>
          <CardHeader><CardTitle>Source & device</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-4 text-sm md:grid-cols-4">
              {[
                ["Source", traffic.referrers ?? []],
                ["Country", traffic.countries ?? []],
                ["Device", traffic.devices ?? []],
                ["Landing Path", traffic.landing_paths ?? []],
              ].map(([title, rows]) => (
                <div key={String(title)} className="space-y-2">
                  <div className="text-xs font-bold text-slate-500">{String(title)}</div>
                  {(rows as TopItem[]).length === 0 ? (
                    <div className="text-xs text-slate-500">No Data</div>
                  ) : (
                    <ul className="space-y-1.5">
                      {(rows as TopItem[]).slice(0, 5).map((item) => (
                        <li key={item.name} className="flex items-center gap-2">
                          <span className="min-w-0 flex-1 truncate" title={item.name}>{item.name}</span>
                          <span className="font-mono text-blue-600">{item.count}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Auth Degradation Reasons</CardTitle></CardHeader>
          <CardContent>
            {(degradedAuth.by_reason ?? []).length === 0 ? (
              <p className="text-sm text-slate-500">No degraded_auth_profile</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {(degradedAuth.by_reason ?? []).map((item) => (
                  <li key={item.name} className="flex items-center gap-3">
                    <span className="min-w-0 flex-1 truncate" title={item.name}>{item.name}</span>
                    <span className="font-mono text-rose-500">{item.count}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Conversion Funnel</CardTitle></CardHeader>
        <CardContent>
          {chartData.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-500">No Data</p>
          ) : (
            <AnalyticsFunnelChart data={chartData} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Stage Details</CardTitle></CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-slate-400">
                  <th className="px-3 py-2 font-medium">Stage</th>
                  <th className="px-3 py-2 text-right font-medium">Count</th>
                  <th className="px-3 py-2 text-right font-medium">Unique Users</th>
                  <th className="px-3 py-2 text-right font-medium">Conv. Rate</th>
                  <th className="px-3 py-2 text-right font-medium">Drop Rate</th>
                </tr>
              </thead>
              <tbody>
                {funnel.map((step, i) => {
                  const pct = step.pct_of_prev;
                  const dropPct = pct != null ? Math.max(0, 100 - pct) : 0;
                  return (
                    <tr key={step.key} className="border-b border-white/5">
                      <td className="px-3 py-2 text-white">{step.label}</td>
                      <td className="px-3 py-2 text-right font-mono text-white">{step.count}</td>
                      <td className="px-3 py-2 text-right font-mono text-white">{step.uniqueActors}</td>
                      <td className="px-3 py-2 text-right text-emerald-400">{pct != null ? `${pct}%` : "—"}</td>
                      <td className="px-3 py-2 text-right text-amber-400">{i > 0 ? `${dropPct}%` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {rates ? (
            <p className="mt-3 text-xs text-slate-500">
              Rates are based on unique actors; Count captures retry and repeat behavior.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
