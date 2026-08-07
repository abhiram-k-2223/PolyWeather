import { getOpsPaidConversionKpi } from "@/lib/ops-conversion";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

export function runTests() {
  const trialKpi = getOpsPaidConversionKpi([
    { key: "landing_view", label: "Landing page visits", count: 909, uniqueActors: 500 },
    { key: "trial_created", label: "Trial created", count: 24, uniqueActors: 20 },
    { key: "payment_success", label: "Payment success", count: 11, uniqueActors: 10 },
  ]);

  assert(trialKpi.rateLabel === "50.0%", "ops overview paid conversion should use trial unique actors before landing sessions");
  assert(trialKpi.subLabel === "Trial 20 → 10 · Visitors 2.0%", "ops overview paid conversion should keep visitor conversion as secondary context");

  const signupKpi = getOpsPaidConversionKpi([
    { key: "landing_view", label: "Landing page visits", count: 100, uniqueActors: 80 },
    { key: "signup_success", label: "Signup success", count: 8, uniqueActors: 8 },
    { key: "payment_success", label: "Payment success", count: 2, uniqueActors: 2 },
  ]);

  assert(signupKpi.rateLabel === "25.0%", "ops overview paid conversion should fall back to signup unique actors when no trial data exists");
  assert(signupKpi.subLabel === "Signup 8 → 2 · Visitors 2.5%", "ops overview paid conversion should label the active denominator");
}
