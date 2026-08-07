type OpsFunnelStep = {
  key?: string;
  label?: string;
  count?: number;
  uniqueActors?: number;
};

type ConversionBase = {
  key: string;
  label: string;
};

const PAID_CONVERSION_BASES: ConversionBase[] = [
  { key: "trial_created", label: "Trial" },
  { key: "signup_success", label: "Signup" },
  { key: "enter_terminal", label: "Terminal" },
  { key: "landing_view", label: "Visitors" },
];

function finiteNonNegative(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
}

function stepValue(step?: OpsFunnelStep) {
  return finiteNonNegative(step?.uniqueActors) || finiteNonNegative(step?.count);
}

function formatRate(numerator: number, denominator: number) {
  if (denominator <= 0) return "—";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

export function getOpsPaidConversionKpi(steps: OpsFunnelStep[]) {
  const stepByKey = Object.fromEntries(
    steps.map((step) => [step.key || step.label || "", step]),
  );
  const paid = stepValue(stepByKey.payment_success);
  const base = PAID_CONVERSION_BASES
    .map((candidate) => ({
      ...candidate,
      value: stepValue(stepByKey[candidate.key]),
    }))
    .find((candidate) => candidate.value > 0);
  const denominator = base?.value ?? 0;
  const landing = stepValue(stepByKey.landing_view);
  const visitorRate = landing > 0 ? formatRate(paid, landing) : "—";
  const visitorContext =
    base && base.key !== "landing_view" && visitorRate !== "—"
      ? ` · Visitors ${visitorRate}`
      : "";

  return {
    rateLabel: formatRate(paid, denominator),
    subLabel: base
      ? `${base.label} ${denominator} → ${paid}${visitorContext}`
      : `Paid ${paid}`,
    numerator: paid,
    denominator,
    denominatorKey: base?.key ?? null,
  };
}
