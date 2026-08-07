export function formatTafMarkerType(type: string) {
  const normalized = String(type || "").trim().toUpperCase();
  return (
    {
      BASE: "Base regime",
      FM: "Hard shift",
      TEMPO: "Temporary swing",
      BECMG: "Gradual shift",
      PROB30: "30% risk window",
      PROB40: "40% risk window",
      "PROB30 TEMPO": "30% temporary swing",
      "PROB40 TEMPO": "40% temporary swing",
    }[normalized] || normalized
  );
}
