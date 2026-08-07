import { __buildDebQualityLabelForTest, __buildTemperatureStatsLabelsForTest } from "@/components/dashboard/scan-terminal/TemperatureStatsBars";
import { temp } from "@/components/dashboard/scan-terminal/utils";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

export function runTests() {
  const hongKong = __buildTemperatureStatsLabelsForTest({
    isEn: true,
    isShenzhen: false,
    runwayHeaderLabel: "Reference station (1m)",
    metarHeaderLabel: "HKO live (10m)",
    runwayHighLabel: "Reference station",
    metarHighLabel: "HKO",
  });

  assert(hongKong.primary === "Reference Station (1m)", "Hong Kong English primary label should match Reference station (1m)");
  assert(hongKong.compactSecondary === "HKO Live (10m)", "Hong Kong compact secondary label should match HKO live (10m)");
  assert(hongKong.expandedSecondary === "HKO Live (10m) · Daily High", "Hong Kong expanded secondary label should include HKO plus Daily High");
  assert(hongKong.runwayHigh === "Reference Station", "Hong Kong high summary should translate Reference station");
  assert(hongKong.metarHigh === "HKO", "Hong Kong high summary should translate HKO");

  const shenzhen = __buildTemperatureStatsLabelsForTest({
    isEn: true,
    isShenzhen: true,
    runwayHeaderLabel: "HKO live (10m)",
    metarHeaderLabel: "HKO live (10m)",
    runwayHighLabel: "HKO live",
    metarHighLabel: "HKO",
  });

  assert(shenzhen.primary === "HKO Live (10m)", "Shenzhen English primary label should match HKO live (10m)");
  assert(shenzhen.compactSecondary === "Daily High", "Shenzhen compact secondary label should match Daily High");
  assert(shenzhen.expandedSecondary === "HKO Live (10m) · Daily High", "Shenzhen expanded secondary label should match HKO live + Daily High");
  assert(shenzhen.runwayHigh === "HKO Live", "Shenzhen high summary should translate HKO live");
  assert(shenzhen.metarHigh === "HKO", "Shenzhen high summary should translate HKO");

  const shanghai = __buildTemperatureStatsLabelsForTest({
    isEn: true,
    isShenzhen: false,
    runwayHeaderLabel: "Runway live (3m)",
    metarHeaderLabel: "METAR settlement (30m)",
    runwayHighLabel: "Runway live",
    metarHighLabel: "METAR official",
  });

  assert(shanghai.primary === "Runway Live (3m)", "AMSC English primary label should match Runway live (3m)");
  assert(shanghai.runwayHigh === "Runway", "AMSC runway high label should remain Runway");

  const zh = __buildTemperatureStatsLabelsForTest({
    isEn: false,
    isShenzhen: true,
    runwayHeaderLabel: "HKO live (10m)",
    metarHeaderLabel: "HKO live (10m)",
    runwayHighLabel: "HKO live",
    metarHighLabel: "HKO",
  });

  assert(zh.primary === "HKO live (10m)", "Chinese primary label should remain unchanged");
  assert(zh.compactSecondary === "Daily High", "Chinese Shenzhen compact secondary label should remain Daily High");

  assert(temp(null, "°C") === "--", "empty temperature values should not render as 0.0°C while city detail is loading");
  assert(temp(undefined, "°C") === "--", "undefined temperature values should not render as 0.0°C while city detail is loading");
  assert(temp("", "°C") === "--", "blank temperature values should not render as 0.0°C while city detail is loading");
  assert(
    __buildDebQualityLabelForTest({ recommendation: "context_only" }, true) === "Context",
    "low-confidence DEB should render as context-only guidance in English",
  );
  assert(
    __buildDebQualityLabelForTest({ recommendation: "insufficient" }, false) === "Thin",
    "thin-sample DEB should render an English thin-sample label",
  );
}
