type FetchOptions = RequestInit & { timeoutMs?: number };

async function opsFetch<T>(url: string, options?: FetchOptions): Promise<T> {
  const res = await fetch(url, { cache: "no-store", ...options });
  if (!res.ok) {
    const detail = (await res.text().catch(() => "")).slice(0, 300);
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const opsApi = {
  health() {
    return opsFetch<{ status: string }>("/api/healthz");
  },
  systemStatus() {
    return opsFetch<Record<string, unknown>>("/api/system/status");
  },
  sourceHealth(limit = 80) {
    return opsFetch<Record<string, unknown>>(`/api/ops/source-health?limit=${limit}`);
  },
  observationCollectorStatus(limit = 200) {
    return opsFetch<Record<string, unknown>>(`/api/ops/observation-collector-status?limit=${limit}`);
  },
  async funnel(days = 30) {
    const raw = await opsFetch<{
      events?: Record<string, { total?: number; unique_users?: number; unique_actors?: number }>;
      diagnostics?: Record<string, { total?: number; unique_actors?: number; by_reason?: { name: string; count: number }[] }>;
      rates?: Record<string, number>;
      traffic?: {
        referrers?: { name: string; count: number }[];
        countries?: { name: string; count: number }[];
        devices?: { name: string; count: number }[];
        landing_paths?: { name: string; count: number }[];
      };
      window_days?: number;
    }>(`/api/ops/analytics/funnel?days=${days}`);
    const stepOrder = ["landing_view", "enter_terminal", "login_start", "signup_success", "trial_created", "payment_start", "payment_success"];
    const stepLabels: Record<string, string> = {
      landing_view: "Landing view",
      enter_terminal: "Enter terminal",
      login_start: "Login start",
      signup_success: "Signup success",
      trial_created: "Trial created",
      payment_start: "Payment start",
      payment_success: "Payment success",
    };
    const steps = stepOrder.map((key, i) => {
      const evt = raw?.events?.[key];
      const count = evt?.total ?? 0;
      const uniqueActors = evt?.unique_actors ?? evt?.unique_users ?? 0;
      let pct_of_prev: number | undefined;
      if (i > 0) {
        const prevCount = raw?.events?.[stepOrder[i - 1]]?.total ?? 0;
        pct_of_prev = prevCount > 0 ? Math.round((count / prevCount) * 100) : 0;
      } else {
        pct_of_prev = 100;
      }
      return { key, label: stepLabels[key] ?? key, count, uniqueActors, pct_of_prev };
    });
    return {
      diagnostics: raw?.diagnostics ?? {},
      rates: raw?.rates,
      steps,
      traffic: raw?.traffic ?? {},
      window_days: raw?.window_days,
    };
  },
  feedback(limit = 100, status?: string) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (status) params.set("status", status);
    return opsFetch<Record<string, unknown>>(`/api/ops/feedback?${params}`);
  },
  updateFeedbackStatus(feedbackId: string | number, status: string) {
    return opsFetch<Record<string, unknown>>(`/api/ops/feedback/${feedbackId}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
  },
  truthHistory(params: Record<string, string>) {
    const qs = new URLSearchParams(params).toString();
    return opsFetch<Record<string, unknown>>(`/api/ops/truth-history?${qs}`);
  },
  trainingAccuracy() {
    return opsFetch<{
      accuracy: Array<{
        city_id: string;
        name: string;
        deb?: {
          hit_rate: number;
          mae: number;
          total_days: number;
          hits?: number;
          details_str: string;
        } | null;
        deb_recent?: {
          recent_7d?: {
            start_date?: string | null;
            end_date?: string | null;
            samples?: number;
            hits?: number;
            hit_rate?: number | null;
            mae?: number | null;
            bias?: number | null;
            city_count?: number;
          };
          recent_14d?: {
            start_date?: string | null;
            end_date?: string | null;
            samples?: number;
            hits?: number;
            hit_rate?: number | null;
            mae?: number | null;
            bias?: number | null;
            city_count?: number;
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
          hits?: number;
          details_str: string;
        } | null;
      }>;
      deb_summary?: {
        historical?: {
          city_count?: number;
          avg_hit_rate?: number | null;
          weighted_hit_rate?: number | null;
          avg_mae?: number | null;
          avg_days_per_city?: number;
          sample_days?: number;
          hits?: number;
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
          start_date?: string | null;
          end_date?: string | null;
          samples?: number;
          hits?: number;
          hit_rate?: number | null;
          mae?: number | null;
          bias?: number | null;
          city_count?: number;
        };
        recent_14d?: {
          start_date?: string | null;
          end_date?: string | null;
          samples?: number;
          hits?: number;
          hit_rate?: number | null;
          mae?: number | null;
          bias?: number | null;
          city_count?: number;
        };
        versions?: Record<string, {
          version?: string;
          samples?: number;
          mae?: number | null;
          rmse?: number | null;
          bias?: number | null;
          bucket_hit_rate?: number | null;
        }>;
      };
    }>("/api/ops/training/accuracy");
  },
};
