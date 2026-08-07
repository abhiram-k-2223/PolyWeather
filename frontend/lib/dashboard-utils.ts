import { Locale } from "@/lib/i18n";
import {
  AiAnalysisStructured,
  CityDetail,
  NearbyStation,
} from "@/lib/dashboard-types";
import {
  getNoaaStationCode,
  getNoaaStationName,
  getObservationSourceCode,
  getObservationSourceTag,
  getRealtimeObservationTag,
  isTurkishMgmCity,
} from "@/lib/observation-source-utils";
import {
  formatTemperatureValue,
  normalizeTemperatureLabel,
  normalizeTemperatureSymbol,
} from "@/lib/temperature-utils";
import { formatTafMarkerType } from "@/lib/taf-utils";
import { normalizeHm } from "@/lib/time-utils";
import { getWeatherSummary } from "@/lib/weather-summary-utils";
import { getDisplayAirportPrimary } from "@/lib/airport-observation-display";

export { getTemperatureChartData } from "@/lib/chart-utils";
export { getModelView, getProbabilityView } from "@/lib/model-utils";
export { getTodayPaceView } from "@/lib/pace-utils";
export {
  getHeroMetaItems,
  getRiskBadgeLabel,
  getWeatherSummary,
  translateMetar,
} from "@/lib/weather-summary-utils";
export {
  formatTemperatureValue,
  normalizeTemperatureLabel,
  normalizeTemperatureSymbol,
} from "@/lib/temperature-utils";

function isEnglish(locale: Locale) {
  return locale === "en-US";
}

function containsCjk(text: string) {
  return /[\u3400-\u9fff]/.test(text);
}

function getLocalizedDynamicCommentary(
  detail: CityDetail,
  locale: Locale,
): { headline: string; bullets: string[]; source: string } {
  const commentary = detail.dynamic_commentary || {};
  const preferEnglish = isEnglish(locale);
  const rawHeadline = preferEnglish
    ? String(commentary.headline_en || "").trim()
    : String(commentary.headline_zh || "").trim();
  const rawBullets = preferEnglish
    ? commentary.bullets_en
    : commentary.bullets_zh;
  const bullets = Array.isArray(rawBullets)
    ? rawBullets.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const fallbackHeadline = String(commentary.summary || "").trim();
  const fallbackBullets = Array.isArray(commentary.notes)
    ? commentary.notes.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  return {
    headline: rawHeadline || fallbackHeadline,
    bullets: bullets.length > 0 ? bullets : fallbackBullets,
    source: String(commentary.source || "").trim(),
  };
}

export function parseAiAnalysis(analysis: CityDetail["ai_analysis"]) {
  const fallback = {
    bullets: [] as string[],
    summary: "",
  };

  if (!analysis) return fallback;

  if (typeof analysis === "string") {
    return {
      bullets: [],
      summary: analysis.trim(),
    };
  }

  const structured = analysis as AiAnalysisStructured;
  return {
    bullets: Array.isArray(structured.highlights)
      ? structured.highlights
      : Array.isArray(structured.points)
        ? structured.points
        : [],
    summary: structured.summary || structured.text || structured.message || "",
  };
}

export function getAirportNarrative(
  detail: CityDetail,
  locale: Locale = "en-US",
) {
  const parsed = parseAiAnalysis(detail.ai_analysis);
  if (!isEnglish(locale)) return parsed;

  const englishSummary = containsCjk(parsed.summary) ? "" : parsed.summary.trim();
  const englishBullets = parsed.bullets
    .map((item) => String(item || "").trim())
    .filter((item) => item && !containsCjk(item));

  if (englishSummary || englishBullets.length > 0) {
    return {
      bullets: englishBullets,
      summary: englishSummary,
    };
  }

  const sourceLabel =
    String(detail.current?.settlement_source_label || "").trim() ||
    String(detail.risk?.icao || "").trim() ||
    String(detail.risk?.airport || "").trim() ||
    String(detail.display_name || detail.name || "").trim() ||
    "Airport";
  const currentTemp = Number(detail.current?.temp);
  const tempText = Number.isFinite(currentTemp)
    ? `${currentTemp}${detail.temp_symbol || "°C"}`
    : null;
  const obsTime = String(detail.current?.obs_time || "").trim();
  const weatherText = getWeatherSummary(detail, locale).weatherText.toLowerCase();
  const windBucket = bucketLabel(
    trendBucketFromDir(detail.current?.wind_dir ?? null),
    locale,
  );
  const windSpeedKt = Number(detail.current?.wind_speed_kt);
  const tafSummary = String(detail.taf?.signal?.summary_en || "").trim();
  const windPhrase = Number.isFinite(windSpeedKt)
    ? `${windBucket} around ${windSpeedKt} kt`
    : `${windBucket} prevailing`;
  const summaryParts = [
    tempText
      ? `${sourceLabel} reports ${tempText}${obsTime ? ` at ${obsTime}` : ""}, ${weatherText}.`
      : `${sourceLabel} reports ${weatherText}${obsTime ? ` at ${obsTime}` : ""}.`,
    `${windPhrase}.`,
    tafSummary,
  ].filter(Boolean);

  const bullets: string[] = [];
  const rawMetar = String(detail.current?.raw_metar || "").trim();
  if (rawMetar) {
    bullets.push(`Latest METAR: ${rawMetar}`);
  }
  if (tafSummary) {
    bullets.push(`TAF signal: ${tafSummary}`);
  }
  if (detail.taf?.raw_taf) {
    bullets.push(`TAF available for airport-side timing checks.`);
  }

  return {
    bullets,
    summary: summaryParts.join(" "),
  };
}

export function pickAnkaraNearbyStations(stations: NearbyStation[]) {
  const preferredNames = [
    "Airport (MGM/17128)",
    "Ankara (Bölge/Center)",
    "Ankara (Bolge/Center)",
    "Etimesgut",
    "Pursaklar",
    "Cubuk",
    "Çubuk",
    "Kalecik",
  ];

  const picks = preferredNames
    .map((name) => stations.find((station) => station?.name === name))
    .filter(Boolean) as NearbyStation[];

  return picks.length ? picks : stations;
}

function distanceKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
) {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) *
      Math.cos(toRad(lat2)) *
      Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function pickMapNearbyStations(detail: CityDetail) {
  const stations = Array.isArray(detail.official_nearby)
    ? detail.official_nearby
    : Array.isArray(detail.mgm_nearby)
      ? detail.mgm_nearby
      : [];
  const city = String(detail.name || detail.display_name || "")
    .trim()
    .toLowerCase();

  if (city === "seoul" || city === "busan") {
    return [];
  }

  if (city === "ankara") {
    return pickAnkaraNearbyStations(stations);
  }

  if (city === "istanbul" && Number.isFinite(detail.lat) && Number.isFinite(detail.lon)) {
    const preferredTokens = [
      "havalimani",
      "havalimanı",
      "arnavutkoy",
      "arnavutköy",
      "liman feneri",
    ];
    const scored = stations
      .map((station) => {
        const lat = Number(station.lat);
        const lon = Number(station.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
          return null;
        }
        const name = String(station.name || "").toLowerCase();
        const preferred = preferredTokens.some((token) => name.includes(token));
        return {
          preferred,
          station,
          km: distanceKm(Number(detail.lat), Number(detail.lon), lat, lon),
        };
      })
      .filter(Boolean) as Array<{
      preferred: boolean;
      station: NearbyStation;
      km: number;
    }>;

    const closePreferred = scored
      .filter((row) => row.preferred && row.km <= 18)
      .sort((a, b) => a.km - b.km)
      .map((row) => row.station);

    const closeFallback = scored
      .filter((row) => row.km <= 8)
      .sort((a, b) => a.km - b.km)
      .map((row) => row.station);

    const merged = [...closePreferred, ...closeFallback].filter(
      (station, index, list) =>
        list.findIndex(
          (row) =>
            row.name === station.name &&
            row.lat === station.lat &&
            row.lon === station.lon,
        ) === index,
    );

    return merged.slice(0, 3);
  }

  return stations;
}

export function getFutureSlice(detail: CityDetail, dateStr: string) {
  const hourly = detail.hourly_next_48h || {};
  const times = hourly.times || [];
  const slice: Array<{
    cloudCover: number | null;
    dewPoint: number | null;
    label: string;
    precipProb: number | null;
    pressure: number | null;
    radiation: number | null;
    temp: number | null;
    time: string;
    windDir: number | null;
    windSpeed: number | null;
  }> = [];

  for (let index = 0; index < times.length; index += 1) {
    const timestamp = times[index];
    if (!timestamp || !String(timestamp).startsWith(dateStr)) continue;

    slice.push({
      cloudCover: hourly.cloud_cover?.[index] ?? null,
      dewPoint: hourly.dew_point?.[index] ?? null,
      label: String(timestamp).split("T")[1]?.slice(0, 5) || timestamp,
      precipProb: hourly.precipitation_probability?.[index] ?? null,
      pressure: hourly.pressure_msl?.[index] ?? null,
      radiation: hourly.radiation?.[index] ?? null,
      temp: hourly.temps?.[index] ?? null,
      time: timestamp,
      windDir: hourly.wind_direction_10m?.[index] ?? null,
      windSpeed: hourly.wind_speed_10m?.[index] ?? null,
    });
  }

  return slice;
}

function trendBucketFromDir(direction?: number | null) {
  const value = Number(direction);
  if (!Number.isFinite(value)) return null;
  if (value >= 135 && value <= 240) return "southerly";
  if (value >= 290 || value <= 45) return "northerly";
  if (value > 45 && value < 135) return "easterly";
  return "westerly";
}

function bucketLabel(bucket: string | null, locale: Locale = "en-US") {
  return (
    {
      southerly: "S / SW wind",
      northerly: "N / NW wind",
      easterly: "E wind",
      westerly: "W wind",
    }[bucket || ""] || "Unknown wind direction"
  );
}

export function wuRound(value: number | null | undefined) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return numeric >= 0
    ? Math.floor(numeric + 0.5)
    : Math.ceil(numeric - 0.5);
}

export function formatDelta(value: number | null | undefined, suffix = "") {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  const sign = numeric > 0 ? "+" : "";
  return `${sign}${numeric.toFixed(1)}${suffix}`;
}

function getForecastTextForDate(detail: CityDetail, dateStr: string) {
  const periods = detail.source_forecasts?.weather_gov?.forecast_periods || [];
  return periods.filter((period) =>
    String(period.start_time || "").startsWith(dateStr),
  );
}

export function computeFrontTrendSignal(
  detail: CityDetail,
  dateStr: string,
  locale: Locale = "en-US",
) {
  const upperAirSignal = detail.vertical_profile_signal || {};
  const tafSignal = detail.taf?.signal || {};
  const upperAirTradeCue = upperAirSignal.source
    ? upperAirSignal.heating_setup === "supportive"
      ? {
          label: 'Trade cue',
          note: 'The setup still supports further warming. Do not call the high too early.',
          tone: "warm",
          value: 'Lean warmer',
        }
      : upperAirSignal.heating_setup === "suppressed"
        ? {
            label: 'Trade cue',
            note: 'Further upside looks less reliable. Do not chase the high blindly.',
            tone: "cold",
            value: 'Lean cautious',
          }
        : {
            label: 'Trade cue',
            note: 'The picture is still mixed. Let the next move decide first.',
            tone: "",
            value: 'Wait / confirm',
          }
    : null;
  const baseUpperAirSummary = upperAirSignal.source
    ? (() => {
        const hasMetrics =
          upperAirSignal.cape_max != null ||
          upperAirSignal.cin_min != null ||
          upperAirSignal.boundary_layer_height_max != null ||
          upperAirSignal.shear_10m_180m_max != null;
        if (!hasMetrics) {
          return 'Upper-air inputs are incomplete. For now, trade direction should rely more on surface structure.';
        }
        if (upperAirSignal.heating_setup === "supportive") {
          return 'Upper-air structure still favors further warming. Do not call the high too early.';
        }
        if (upperAirSignal.heating_setup === "suppressed") {
          return 'Upper-air structure leans toward capping the afternoon high. Further upside looks less reliable.';
        }
        return 'Upper-air structure is fairly neutral. It does not provide a clean edge yet.';
      })()
    : "";
  const tafSummary =
    tafSignal.available && dateStr === detail.local_date
      ? isEnglish(locale)
        ? String(tafSignal.summary_en || "").trim()
        : String(tafSignal.summary_zh || "").trim()
      : "";
  let upperAirSummary = baseUpperAirSummary;
  let tafMetric:
    | {
        label: string;
        note: string;
        tone?: string;
        value: string;
      }
    | null = null;
  let upperAirMetrics: Array<{
    label: string;
    note: string;
    tone?: string;
    value: string;
  }> = [];
  const localizedCommentary =
    dateStr === detail.local_date
      ? getLocalizedDynamicCommentary(detail, locale)
      : { headline: "", bullets: [], source: "" };
  const backendSummary =
    localizedCommentary.headline &&
    (!isEnglish(locale) || !containsCjk(localizedCommentary.headline))
      ? localizedCommentary.headline
      : "";
  const backendNotes = localizedCommentary.bullets.filter(
    (note) => !isEnglish(locale) || !containsCjk(note),
  );
  const slice = getFutureSlice(detail, dateStr);
  const currentTemp = Number(detail.current?.temp);
  const currentDew = Number(detail.current?.dewpoint);

  if (!slice.length) {
    const fallbackSummary =
      backendSummary ||
      ('Insufficient intraday structured data. Keep baseline monitoring.');
    return {
      confidence: "low",
      label: 'Monitoring',
      metrics: [] as Array<{
        label: string;
        note: string;
        tone?: string;
        value: string;
      }>,
      upperAirMetrics,
      upperAirSummary,
      precipMax: 0,
      score: 0,
      summary: fallbackSummary,
      summaryLines: [fallbackSummary],
      weatherGovPeriods: [] as ReturnType<typeof getForecastTextForDate>,
    };
  }

  const normalizeHm = (value: unknown): string | null => {
    const match = String(value || "").match(/(\d{1,2}):(\d{2})/);
    if (!match) return null;
    const hour = Number.parseInt(match[1], 10);
    const minute = Number.parseInt(match[2], 10);
    if (
      !Number.isFinite(hour) ||
      !Number.isFinite(minute) ||
      hour < 0 ||
      hour > 23 ||
      minute < 0 ||
      minute > 59
    ) {
      return null;
    }
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  };
  const hmToMinutes = (value: string | null) => {
    if (!value) return null;
    const [hourPart, minutePart] = value.split(":");
    const hour = Number.parseInt(hourPart || "", 10);
    const minute = Number.parseInt(minutePart || "", 10);
    if (
      !Number.isFinite(hour) ||
      !Number.isFinite(minute) ||
      hour < 0 ||
      hour > 23 ||
      minute < 0 ||
      minute > 59
    ) {
      return null;
    }
    return hour * 60 + minute;
  };
  const pointMinutes = (point: { label?: string }) =>
    hmToMinutes(normalizeHm(point.label));

  const isTargetToday = dateStr === detail.local_date;
  const currentHm = normalizeHm(detail.local_time);
  const sunsetHm = normalizeHm(detail.forecast?.sunset);
  const peakFirstHour = Number(detail.peak?.first_h);
  const peakLastHour = Number(detail.peak?.last_h);
  const hasPeakWindow =
    Number.isFinite(peakFirstHour) &&
    Number.isFinite(peakLastHour) &&
    peakFirstHour >= 0 &&
    peakLastHour >= peakFirstHour;
  const currentMinutes = hmToMinutes(currentHm);
  const sunsetMinutes = hmToMinutes(sunsetHm);
  const peakWindowStartMinutes = hasPeakWindow
    ? Math.max(0, (peakFirstHour - 2) * 60)
    : null;
  const peakWindowEndMinutes = hasPeakWindow
    ? Math.min(23 * 60 + 59, (peakLastHour + 1) * 60)
    : null;
  const canUseSunsetWindow =
    isTargetToday &&
    currentMinutes !== null &&
    sunsetMinutes !== null &&
    sunsetMinutes > currentMinutes;
  const tafMarkers = Array.isArray(tafSignal.markers) ? tafSignal.markers : [];
  const markerSummary = (
    marker:
      | {
          marker_type?: string | null;
          start_local?: string | null;
          end_local?: string | null;
          suppression_level?: string | null;
          summary_zh?: string | null;
          summary_en?: string | null;
        }
      | null
      | undefined,
  ) =>
    !marker
      ? ""
      : isEnglish(locale)
        ? String(marker.summary_en || "").trim()
        : String(marker.summary_zh || "").trim();
  const currentTafMarker =
    tafSignal.available && currentMinutes !== null
      ? tafMarkers.find((marker) => {
          const start = hmToMinutes(normalizeHm(marker?.start_local));
          const end = hmToMinutes(normalizeHm(marker?.end_local));
          if (start === null || end === null) return false;
          return currentMinutes >= start && currentMinutes <= end;
        })
      : null;
  const nextTafMarker =
    tafSignal.available && currentMinutes !== null && !currentTafMarker
      ? tafMarkers.find((marker) => {
          const start = hmToMinutes(normalizeHm(marker?.start_local));
          return start !== null && start > currentMinutes;
        })
      : null;
  const sameMarker = (
    left:
      | { marker_type?: string | null; start_local?: string | null; end_local?: string | null }
      | null
      | undefined,
    right:
      | { marker_type?: string | null; start_local?: string | null; end_local?: string | null }
      | null
      | undefined,
  ) =>
    !!left &&
    !!right &&
    String(left.marker_type || "") === String(right.marker_type || "") &&
    String(left.start_local || "") === String(right.start_local || "") &&
    String(left.end_local || "") === String(right.end_local || "");
  const formatTafSegmentBody = (
    marker:
      | {
          marker_type?: string | null;
          start_local?: string | null;
          end_local?: string | null;
          suppression_level?: string | null;
        }
      | null
      | undefined,
    kind: "current" | "next" | "peak",
  ) => {
    if (!marker) return "";
    const range = `${normalizeHm(marker.start_local) || "--:--"}-${normalizeHm(marker.end_local) || "--:--"}`;
    const typeLabel = formatTafMarkerType(
      String(marker.marker_type || ""),
    );
    const level = String(marker.suppression_level || "low").toLowerCase();
    const statusText = level === "high"
        ? "shows shower or thunderstorm disruption"
        : level === "medium"
          ? "shows cloud or light-rain disruption"
          : "stays relatively stable";
    return `${typeLabel} (${range}), ${statusText}.`;
  };
  const peakWindowTafMarker =
    tafSignal.available &&
    peakWindowStartMinutes !== null &&
    peakWindowEndMinutes !== null
      ? tafMarkers.find((marker) => {
          const start = hmToMinutes(normalizeHm(marker?.start_local));
          const end = hmToMinutes(normalizeHm(marker?.end_local));
          if (start === null || end === null) return false;
          return start <= peakWindowEndMinutes && end >= peakWindowStartMinutes;
        })
      : null;
  const peakTafSummary = markerSummary(peakWindowTafMarker);
  const peakTafStillRelevant =
    peakWindowEndMinutes === null ||
    currentMinutes === null ||
    currentMinutes < peakWindowEndMinutes;
  const effectivePeakTafSummary =
    peakTafStillRelevant &&
    peakTafSummary &&
    !sameMarker(peakWindowTafMarker, currentTafMarker) &&
    !sameMarker(peakWindowTafMarker, nextTafMarker)
      ? peakTafSummary
      : "";
  const currentTafLine = currentTafMarker
    ? `Use current TAF as primary: ${formatTafSegmentBody(currentTafMarker, "current")}`
    : nextTafMarker
      ? `Use next TAF segment as primary: ${formatTafSegmentBody(nextTafMarker, "next")}`
      : "";
  const currentTafLineForSummary = currentTafLine.replace(/^Use (current|next) TAF (as primary|segment as primary):\s*/i, "");
  const peakTafLine =
    effectivePeakTafSummary && peakWindowTafMarker
      ? `Peak-window reference: ${formatTafSegmentBody(peakWindowTafMarker, "peak")}`
      : "";
  const peakTafLineForSummary = peakTafLine.replace(/^Peak-window reference:\s*/i, "");
  const tafFallbackSummary =
    currentTafLine || peakTafLine ? "" : tafSummary;
  const tafPrimarySummary = currentTafMarker
    ? `Use current TAF as primary: ${currentTafLineForSummary}`
    : nextTafMarker
      ? `Use next TAF segment as primary: ${currentTafLineForSummary}`
      : "";
  const tafReferenceSummary =
    peakTafLineForSummary &&
    peakTafLineForSummary !== currentTafLineForSummary
      ? `Peak-window reference: ${peakTafLineForSummary}`
      : "";
  upperAirSummary = [
    baseUpperAirSummary,
    tafPrimarySummary,
    tafReferenceSummary,
    tafFallbackSummary,
  ]
    .filter(Boolean)
    .join();
  tafMetric =
    tafSignal.available && dateStr === detail.local_date
      ? {
          label: 'Airport TAF',
          note:
            tafPrimarySummary ||
            tafReferenceSummary ||
            tafFallbackSummary ||
            ('Airport TAF is available for the current peak window.'),
          tone:
            tafSignal.suppression_level === "high"
              ? "cold"
              : tafSignal.suppression_level === "low"
                ? "warm"
                : "",
          value:
            tafSignal.suppression_level === "high"
              ? 'Suppression watch'
              : tafSignal.suppression_level === "medium"
                ? 'Watch clouds/rain'
                : 'Mostly stable',
        }
      : null;
  upperAirMetrics = upperAirSignal.source
    ? [
        ...(upperAirTradeCue ? [upperAirTradeCue] : []),
        {
          label: 'Peak setup',
          note:
            upperAirSignal.heating_setup === "supportive"
              ? 'Still supportive of more daytime heating. Do not call the high too early.'
              : upperAirSignal.heating_setup === "suppressed"
                ? 'Leans toward capping the afternoon peak. Further upside looks less reliable.'
                : 'Neutral on its own. It does not provide a clear directional edge yet.',
          tone:
            upperAirSignal.heating_setup === "supportive"
              ? "warm"
              : upperAirSignal.heating_setup === "suppressed"
                ? "cold"
                : "",
          value:
            upperAirSignal.heating_setup === "supportive"
              ? 'Supportive'
              : upperAirSignal.heating_setup === "suppressed"
                ? 'Suppressed'
                : 'Neutral',
        },
        {
          label: 'Peak suppression risk',
          note:
            upperAirSignal.cape_max != null || upperAirSignal.cin_min != null
              ? `How likely clouds or showers are to cap the high. CAPE ${Math.round(Number(upperAirSignal.cape_max ?? 0))}, CIN ${Number(upperAirSignal.cin_min ?? 0).toFixed(0)}.`
              : 'Estimated from the next 48h upper-air profile.',
          tone:
            upperAirSignal.suppression_risk === "high"
              ? "cold"
              : upperAirSignal.suppression_risk === "low"
                ? "warm"
                : "",
          value:
            upperAirSignal.suppression_risk === "high"
              ? 'High'
              : upperAirSignal.suppression_risk === "medium"
                ? 'Medium'
                : 'Low',
        },
        {
          label: 'Afternoon disruption',
          note:
            upperAirSignal.lifted_index_min != null
              ? `How easily the afternoon can turn noisy. Lifted Index ${Number(upperAirSignal.lifted_index_min).toFixed(1)}.`
              : 'Uses instability and lifted-index structure.',
          tone:
            upperAirSignal.trigger_risk === "high"
              ? "cold"
              : upperAirSignal.trigger_risk === "low"
                ? "warm"
                : "",
          value:
            upperAirSignal.trigger_risk === "high"
              ? 'High'
              : upperAirSignal.trigger_risk === "medium"
                ? 'Medium'
                : 'Low',
        },
        {
          label: 'Heating efficiency',
          note:
            upperAirSignal.boundary_layer_height_max != null
              ? `How efficiently surface warmth can keep translating upward. Mixing depth peaks near ${Math.round(Number(upperAirSignal.boundary_layer_height_max))} m.`
              : 'Tracks daytime mixing depth.',
          tone:
            upperAirSignal.mixing_strength === "strong"
              ? "warm"
              : upperAirSignal.mixing_strength === "weak"
                ? "cold"
                : "",
          value:
            upperAirSignal.mixing_strength === "strong"
              ? 'Strong'
              : upperAirSignal.mixing_strength === "medium"
                ? 'Medium'
                : 'Weak',
        },
        ...(tafMetric ? [tafMetric] : []),
      ]
    : tafMetric
      ? [tafMetric]
      : [];

  const futureSlice =
    isTargetToday && currentMinutes !== null
      ? slice.filter((point) => {
          const minutes = pointMinutes(point);
          return minutes === null ? true : minutes >= currentMinutes;
        })
      : slice;
  const untilSunsetSlice =
    canUseSunsetWindow && sunsetMinutes !== null
      ? futureSlice.filter((point) => {
          const minutes = pointMinutes(point);
          return minutes === null ? false : minutes <= sunsetMinutes;
        })
      : futureSlice;
  const aroundPeakSlice =
    isTargetToday &&
    peakWindowStartMinutes !== null &&
    peakWindowEndMinutes !== null
      ? futureSlice.filter((point) => {
          const minutes = pointMinutes(point);
          return minutes === null
            ? false
            : minutes >= peakWindowStartMinutes && minutes <= peakWindowEndMinutes;
        })
      : [];
  const workingSlice =
    aroundPeakSlice.length >= 2
      ? aroundPeakSlice
      : untilSunsetSlice.length >= 2
      ? untilSunsetSlice
      : futureSlice.length >= 2
        ? futureSlice
        : slice;
  const usingPeakWindow = aroundPeakSlice.length >= 2;
  const usingSunsetWindow = canUseSunsetWindow && untilSunsetSlice.length >= 2;
  const first = workingSlice[0] || slice[0];
  const last = workingSlice[workingSlice.length - 1] || slice[slice.length - 1];
  const effectiveHours = Math.max(1, workingSlice.length);
  const windowLabel = `${first?.label || "--"}-${last?.label || "--"}`;
  const windowText = usingPeakWindow
      ? `today ${windowLabel} (~${effectiveHours}h, around peak window)`
      : usingSunsetWindow
      ? `today ${windowLabel} (~${effectiveHours}h, now -> sunset)`
      : isTargetToday
        ? `today ${windowLabel} (~${effectiveHours}h)`
        : `daily ${windowLabel} (~${effectiveHours}h)`;
  const firstTemp = Number.isFinite(Number(first.temp)) ? Number(first.temp) : currentTemp;
  const lastTemp = Number.isFinite(Number(last.temp)) ? Number(last.temp) : firstTemp;
  const tempDelta =
    Number.isFinite(firstTemp) && Number.isFinite(lastTemp) ? lastTemp - firstTemp : 0;
  const firstDew = Number.isFinite(Number(first.dewPoint))
    ? Number(first.dewPoint)
    : currentDew;
  const lastDew = Number.isFinite(Number(last.dewPoint))
    ? Number(last.dewPoint)
    : firstDew;
  const dewDelta =
    Number.isFinite(firstDew) && Number.isFinite(lastDew) ? lastDew - firstDew : 0;
  const firstPressure = Number.isFinite(Number(first.pressure))
    ? Number(first.pressure)
    : null;
  const lastPressure = Number.isFinite(Number(last.pressure))
    ? Number(last.pressure)
    : firstPressure;
  const pressureDelta =
    Number.isFinite(Number(firstPressure)) && Number.isFinite(Number(lastPressure))
      ? Number(lastPressure) - Number(firstPressure)
      : 0;
  const firstCloud = Number.isFinite(Number(first.cloudCover))
    ? Number(first.cloudCover)
    : null;
  const lastCloud = Number.isFinite(Number(last.cloudCover))
    ? Number(last.cloudCover)
    : firstCloud;
  const cloudDelta =
    Number.isFinite(Number(firstCloud)) && Number.isFinite(Number(lastCloud))
      ? Number(lastCloud) - Number(firstCloud)
      : 0;
  const precipMax = slice.reduce(
    (max, point) => Math.max(max, Number(point.precipProb) || 0),
    0,
  );
  const precipWindowSource =
    futureSlice.length >= 2 ? futureSlice : slice;
  const precipCandidates = precipWindowSource
    .map((point) => ({
      label: normalizeHm(point.label),
      minute: pointMinutes(point),
      precip: Number(point.precipProb) || 0,
    }))
    .filter(
      (point): point is { label: string; minute: number; precip: number } =>
        point.label != null && point.minute != null,
    );
  const precipThreshold = precipMax >= 70 ? 50 : precipMax >= 40 ? 40 : 0;
  const precipWindows: Array<{
    start: number;
    end: number;
    startLabel: string;
    endLabel: string;
    peak: number;
  }> = [];
  if (precipThreshold > 0) {
    let active:
      | {
          start: number;
          end: number;
          startLabel: string;
          endLabel: string;
          peak: number;
        }
      | null = null;
    for (const point of precipCandidates) {
      if (point.precip >= precipThreshold) {
        if (!active) {
          active = {
            start: point.minute,
            end: point.minute,
            startLabel: point.label,
            endLabel: point.label,
            peak: point.precip,
          };
        } else {
          active.end = point.minute;
          active.endLabel = point.label;
          active.peak = Math.max(active.peak, point.precip);
        }
      } else if (active) {
        precipWindows.push(active);
        active = null;
      }
    }
    if (active) precipWindows.push(active);
  }
  const primaryPrecipWindow =
    precipWindows.length > 0
      ? [...precipWindows].sort((left, right) => {
          if (right.peak !== left.peak) return right.peak - left.peak;
          return (right.end - right.start) - (left.end - left.start);
        })[0]
      : null;
  const precipWindowLabel = primaryPrecipWindow
    ? `${primaryPrecipWindow.startLabel}-${primaryPrecipWindow.endLabel}`
    : "";
  const precipPeakOverlap =
    primaryPrecipWindow &&
    peakWindowStartMinutes != null &&
    peakWindowEndMinutes != null
      ? Math.max(
          0,
          Math.min(primaryPrecipWindow.end, peakWindowEndMinutes) -
            Math.max(primaryPrecipWindow.start, peakWindowStartMinutes),
        )
      : 0;
  const precipOverlapLevel =
    primaryPrecipWindow && peakWindowStartMinutes != null && peakWindowEndMinutes != null
      ? precipPeakOverlap >= 120
        ? "high"
        : precipPeakOverlap > 0
          ? "medium"
          : "low"
      : "unknown";
  const precipWindowSummary = (() => {
    if (!primaryPrecipWindow) {
      return 'No concentrated model precipitation window is visible yet.';
    }
    const overlapText = precipOverlapLevel === "high"
        ? "It overlaps heavily with the peak window."
        : precipOverlapLevel === "medium"
          ? "It overlaps part of the peak window."
          : "It sits mostly outside the peak window.";
    return `Model precipitation window is ${precipWindowLabel}, peaking near ${Math.round(primaryPrecipWindow.peak)}%. ${overlapText}`;
  })();
  const firstBucket = trendBucketFromDir(first.windDir);
  const lastBucket = trendBucketFromDir(last.windDir);
  const weatherGovPeriods = getForecastTextForDate(detail, dateStr);
  const weatherGovText = weatherGovPeriods
    .map(
      (period) =>
        `${period.short_forecast || ""} ${period.detailed_forecast || ""}`.toLowerCase(),
    )
    .join(" ");

  let warmScore = 0;
  let coldScore = 0;
  if (tempDelta >= 2) warmScore += 24;
  else if (tempDelta >= 0.8) warmScore += 12;
  if (tempDelta <= -2) coldScore += 24;
  else if (tempDelta <= -0.8) coldScore += 12;
  if (dewDelta >= 1.2) warmScore += 14;
  if (dewDelta <= -1.2) coldScore += 10;
  if (pressureDelta >= 1.2) coldScore += 16;
  if (pressureDelta <= -1.0) warmScore += 8;
  if (lastBucket === "southerly") warmScore += 14;
  if (firstBucket !== lastBucket && lastBucket === "southerly") warmScore += 10;
  if (lastBucket === "northerly") coldScore += 14;
  if (firstBucket !== lastBucket && lastBucket === "northerly") coldScore += 10;
  if (cloudDelta >= 15 && tempDelta >= 0) warmScore += 6;
  if (cloudDelta >= 15 && tempDelta < 0) coldScore += 8;
  if (precipMax >= 40) coldScore += 8;
  if (
    weatherGovText.includes("cold front") ||
    weatherGovText.includes("temperatures falling")
  ) {
    coldScore += 18;
  }
  if (weatherGovText.includes("warm front") || weatherGovText.includes("warmer")) {
    warmScore += 18;
  }
  if (weatherGovText.includes("thunder") || weatherGovText.includes("snow")) {
    coldScore += 8;
  }

  const score = Math.max(-100, Math.min(100, warmScore - coldScore));
  const warmLabel = 'Near-term warming bias';
  const coldLabel = 'Near-term cooling bias';
  const monitorLabel = 'Direction unclear';
  const label = score >= 18 ? warmLabel : score <= -18 ? coldLabel : monitorLabel;
  const confidence =
    Math.abs(score) >= 45 ? "high" : Math.abs(score) >= 22 ? "medium" : "low";
  const directionalLead = (() => {
    if (score >= 18 && tempDelta >= 0.5) {
      return `Over ${windowText}, temperatures still lean warmer.`;
    }
    if (score <= -18 && tempDelta <= -0.5) {
      return `Over ${windowText}, temperatures still lean cooler.`;
    }
    if (score >= 18) {
      return `Over ${windowText}, the structure still leans warmer, but the warming pace is not strong yet.`;
    }
    if (score <= -18) {
      return `Over ${windowText}, the structure still leans cooler, but the cooling pace is not decisive yet.`;
    }
    if (tempDelta >= 0.8) {
      return `Over ${windowText}, temperatures still lean warmer, but confidence is limited.`;
    }
    if (tempDelta <= -0.8) {
      return `Over ${windowText}, temperatures still lean cooler, but confidence is limited.`;
    }
    return `Over ${windowText}, temperatures are more likely to stay range-bound for now.`;
  })();
  const summary = (() => {
    const parts: string[] = [];

    if (isEnglish(locale)) {
      parts.push(directionalLead);

      if (lastBucket === "southerly" && firstBucket !== "southerly") {
        parts.push("Low-level wind turns more southerly.");
      } else if (lastBucket === "northerly" && firstBucket !== "northerly") {
        parts.push("Low-level wind shifts toward a northerly regime.");
      }

      if (tempDelta >= 0.8) {
        parts.push(`Temperature rises by ${formatDelta(tempDelta, detail.temp_symbol)}.`);
      } else if (tempDelta <= -0.8) {
        parts.push(`Temperature eases by ${formatDelta(tempDelta, detail.temp_symbol)}.`);
      }

      if (dewDelta >= 0.8) {
        parts.push("Dew point is lifting, suggesting moisture transport is strengthening.");
      } else if (dewDelta <= -0.8) {
        parts.push("Dew point is falling, so low-level air is turning drier.");
      }

      if (cloudDelta >= 15) {
        parts.push("Cloud cover is building.");
      } else if (cloudDelta <= -15) {
        parts.push("Cloud cover is easing.");
      }

      if (pressureDelta >= 1) {
        parts.push("Pressure rebound argues for a cooler push.");
      } else if (pressureDelta <= -1) {
        parts.push("Pressure is softening, which is less hostile to warming.");
      }

      if (precipMax >= 50) {
        parts.push(
          primaryPrecipWindow
            ? `Model precipitation risk concentrates in ${precipWindowLabel}.`
            : "Precipitation risk is high enough to watch for cloud/rain suppression.",
        );
      }

      if (!parts.length) {
        parts.push(`Structured trend is mixed, so the core judgement still centers on ${windowText}.`);
      } else {
        parts.push(`Core judgement remains focused on ${windowText}.`);
      }
    }

    return parts.join();
  })();
  const tafContrastSummary =
    tafSignal.available && dateStr === detail.local_date
      ? (() => {
          const tafSuppression = String(
            tafSignal.suppression_level || "low",
          ).toLowerCase();
          const isCoolingBias = score <= -18;
          const isWarmingBias = score >= 18;

          if (tafSuppression === "low" && isCoolingBias) {
            return 'TAF is not adding a new cloud/rain suppression signal, but the near-surface window is already leaning cooler, so the current cooling bias still comes mainly from surface structure.';
          }
          if (tafSuppression === "low" && isWarmingBias) {
            return 'TAF is not adding a new cloud/rain cap, and the warmer bias still comes mainly from the surface window.';
          }
          if (tafSuppression === "medium" && isCoolingBias) {
            return 'TAF is not the only driver here; it only reinforces part of the cooling-side case, while the main tilt still comes from the surface window.';
          }
          return "";
        })()
      : "";
  const tafSummaryLineBody = [
    tafPrimarySummary,
    tafReferenceSummary,
    tafFallbackSummary,
    tafContrastSummary,
  ]
    .filter(Boolean)
    .join();
  const surfaceSummaryLine = summary
    ? `Surface: ${summary}`
    : "";
  const tafSummaryLine = tafSummaryLineBody
    ? `Airport TAF: ${tafSummaryLineBody}`
    : "";
  const summaryLines = [surfaceSummaryLine, tafSummaryLine].filter(Boolean);
  const combinedSummary = summaryLines.join();
  const cloudNote = (() => {
    if (cloudDelta >= 15 && tempDelta >= 0.8 && dewDelta >= 0.8) {
      return 'Clouds are increasing while temperature and dew point still rise; this usually fits ongoing warm-moist transport rather than immediate cooling.';
    }
    if (cloudDelta >= 15 && tempDelta >= 0 && lastBucket === "southerly") {
      return 'Clouds are building without clear cooling, and the low-level wind still leans southerly; watch for warm advection to continue.';
    }
    if (cloudDelta >= 15 && tempDelta < 0 && precipMax >= 40) {
      return 'Clouds are thickening while temperature eases and precipitation risk is elevated; cloud/rain suppression is becoming more likely.';
    }
    if (cloudDelta >= 15 && tempDelta < 0 && pressureDelta >= 1) {
      return 'Clouds are increasing while temperature softens and pressure rebounds; watch for cold-air push or frontal suppression.';
    }
    if (cloudDelta <= -15 && tempDelta >= 0.8) {
      return 'Cloud cover is easing while temperature rises; daytime heating efficiency is improving.';
    }
    return 'Read forecast cloud-cover increase together with temperature, dew point, wind, and precipitation; it does not override the current observed sky condition.';
  })();
  const dewNote = (() => {
    if (dewDelta >= 1.2 && tempDelta >= 0.8) {
      return 'Dew point and temperature rise together, which usually supports strengthening warm-moist transport.';
    }
    if (dewDelta >= 1.2 && precipMax >= 40) {
      return 'Moisture is building while precipitation risk is already notable; watch for showers to cap daytime heating.';
    }
    if (dewDelta <= -1.2 && tempDelta <= 0) {
      return 'Drier low-level air is arriving together with softer temperature, which leans away from warm-moist support.';
    }
    return 'Use dew-point change to judge whether low-level warm-moist transport is strengthening or fading.';
  })();
  const pressureNote = (() => {
    if (pressureDelta >= 1.2 && tempDelta <= -0.8) {
      return 'Pressure rebound with cooling usually points to a cooler push or frontal suppression.';
    }
    if (pressureDelta <= -1.0 && tempDelta >= 0.8) {
      return 'Pressure is softening while temperature rises, a setup less hostile to warming.';
    }
    return 'Pressure change is used as a supporting signal for cold-air push versus warming resilience.';
  })();
  const windNote = (() => {
    if (firstBucket !== lastBucket && lastBucket === "southerly") {
      return 'Wind turns toward a southerly regime, which is more favorable for warming.';
    }
    if (firstBucket !== lastBucket && lastBucket === "northerly") {
      return 'Wind turns toward a northerly regime, which is more favorable for cooling.';
    }
    if (lastBucket === "southerly") {
      return 'Low-level flow remains southerly, so warm advection has not been disrupted.';
    }
    if (lastBucket === "northerly") {
      return 'Low-level flow remains northerly, so cooling-side support is still present.';
    }
    return 'Wind-direction change matters most when it crosses into southerly or northerly buckets.';
  })();
  const precipNote = (() => {
    if (precipMax >= 60) {
      return `${precipWindowSummary} Cloud/rain suppression can materially change the peak outcome.`;
    }
    if (precipMax >= 40) {
      return `${precipWindowSummary} Watch whether cloud and showers interrupt daytime heating.`;
    }
    return 'Precipitation risk remains limited and is used mainly as a suppression check.';
  })();

  const metrics = [
    {
      label: 'Temperature delta',
      note: `Official Open-Meteo hourly data; window: ${windowText}`,
      tone: tempDelta >= 0.8 ? "warm" : tempDelta <= -0.8 ? "cold" : "",
      value: formatDelta(tempDelta, detail.temp_symbol),
    },
    {
      label: 'Dew point delta',
      note: dewNote,
      tone: dewDelta >= 0.8 ? "warm" : dewDelta <= -0.8 ? "cold" : "",
      value: formatDelta(dewDelta, detail.temp_symbol),
    },
    {
      label: 'Pressure delta',
      note: pressureNote,
      tone: pressureDelta >= 1 ? "cold" : pressureDelta <= -1 ? "warm" : "",
      value: formatDelta(pressureDelta, " hPa"),
    },
    {
      label: 'Wind-direction evolution',
      note: windNote,
      value: `${bucketLabel(firstBucket, locale)} -> ${bucketLabel(lastBucket, locale)}`,
    },
    {
      label: 'Precip window',
      note: precipNote,
      tone: precipMax >= 50 ? "cold" : "",
      value: primaryPrecipWindow
        ? `${precipWindowLabel} · ${Math.round(precipMax)}%`
        : `${Math.round(precipMax)}%`,
    },
    {
      label: 'Forecast cloud-cover delta',
      note: cloudNote,
      tone:
        cloudDelta >= 15 && tempDelta >= 0
          ? "warm"
          : cloudDelta >= 15 && tempDelta < 0
            ? "cold"
            : "",
      value: formatDelta(cloudDelta, "%"),
    },
  ];

  if (backendNotes.length) {
    const normalizedSummary = backendSummary.trim();
    const alignedNotes = backendNotes.filter(
      (note) => String(note || "").trim() !== normalizedSummary,
    );
    alignedNotes.slice(0, metrics.length).forEach((note, index) => {
      if (!note) return;
      metrics[index] = {
        ...metrics[index],
        note,
      };
    });
  }

  return {
    confidence,
    label,
    metrics,
    upperAirMetrics,
    upperAirSummary,
    precipOverlapLevel,
    precipWindowLabel,
    precipMax,
    score,
    summary: combinedSummary || backendSummary || summary,
    summaryLines,
    weatherGovPeriods,
  };
}

export function getFutureModalView(
  detail: CityDetail,
  dateStr: string,
  locale: Locale = "en-US",
) {
  const forecastEntry =
    detail.forecast?.daily?.find((item) => item.date === dateStr) || null;
  const dailyModel = detail.multi_model_daily?.[dateStr] || {};
  const probabilities = dailyModel.probabilities || [];
  const totalProbability = probabilities.reduce((sum, item) => {
    const probability = Number(item.probability);
    return Number.isFinite(probability) ? sum + probability : sum;
  }, 0);
  const weightedProbability = probabilities.reduce((sum, item) => {
    const value = Number(item.value);
    const probability = Number(item.probability);
    if (!Number.isFinite(value) || !Number.isFinite(probability)) {
      return sum;
    }
    return sum + value * probability;
  }, 0);
  const mu = totalProbability > 0 ? weightedProbability / totalProbability : null;
  const deb = dailyModel.deb?.prediction ?? forecastEntry?.max_temp ?? null;

  return {
    deb,
    forecastEntry,
    front: computeFrontTrendSignal(detail, dateStr, locale),
    models: dailyModel.models || {},
    mu: Number.isFinite(Number(mu)) ? Number(mu) : null,
    probabilities,
    slice: getFutureSlice(detail, dateStr),
  };
}

export function getShortTermNowcastLines(
  detail: CityDetail,
  dateStr: string,
  locale: Locale = "en-US",
) {
  const slice = getFutureSlice(detail, dateStr);
  if (dateStr !== detail.local_date) {
    const afternoon = slice.filter((point) => {
      const hour = Number.parseInt(String(point.label).split(":")[0], 10);
      return Number.isFinite(hour) && hour >= 12 && hour <= 18;
    });
    const target = afternoon.length ? afternoon : slice;
    if (!target.length) {
      return [
        ['Target date', dateStr],
        [
          'Peak window',
          'No sufficient hourly forecast data for target-day peak-window diagnostics.',
        ],
      ] as const;
    }

    const maxIndex = target.reduce((bestIndex, point, index, array) => {
      const temp = Number(point.temp);
      const bestTemp = Number(array[bestIndex]?.temp);
      if (!Number.isFinite(temp)) return bestIndex;
      if (!Number.isFinite(bestTemp) || temp > bestTemp) return index;
      return bestIndex;
    }, 0);

    const peakSlice = target.slice(
      Math.max(0, maxIndex - 1),
      Math.min(target.length, maxIndex + 2),
    );
    const start = peakSlice[0];
    const end = peakSlice[peakSlice.length - 1];
    const peakPoint = target[maxIndex] || end;
    const startTemp = Number(start.temp);
    const endTemp = Number(end.temp);
    const startDew = Number(start.dewPoint);
    const endDew = Number(end.dewPoint);
    const startPressure = Number(start.pressure);
    const endPressure = Number(end.pressure);
    const precipValues = peakSlice
      .map((point) => Number(point.precipProb))
      .filter(Number.isFinite);
    const cloudValues = peakSlice
      .map((point) => Number(point.cloudCover))
      .filter(Number.isFinite);
    const maxPrecip = precipValues.length ? Math.max(...precipValues) : 0;
    const maxCloud = cloudValues.length ? Math.max(...cloudValues) : 0;

    return [
      ['Target date', dateStr],
      [
        'Peak window',
        `${start.label} - ${end.label} (prefer 12:00-18:00)`,
      ],
      [
        'Peak estimate',
        `${Number.isFinite(Number(peakPoint.temp)) ? Number(peakPoint.temp).toFixed(1) : "--"}${detail.temp_symbol} @ ${peakPoint.label || "--"}`,
      ],
      [
        'Window temperature',
        `${Number.isFinite(startTemp) ? startTemp.toFixed(1) : "--"}${detail.temp_symbol} -> ${Number.isFinite(endTemp) ? endTemp.toFixed(1) : "--"}${detail.temp_symbol} (${formatDelta(endTemp - startTemp, detail.temp_symbol)})`,
      ],
      [
        'Dew-point delta',
        `${formatDelta(endDew - startDew, detail.temp_symbol)} for diagnosing warm/wet transport in afternoon.`,
      ],
      [
        'Wind shift',
        `${bucketLabel(trendBucketFromDir(start.windDir), locale)} -> ${bucketLabel(trendBucketFromDir(end.windDir), locale)} around peak window.`,
      ],
      [
        'Pressure delta',
        `${formatDelta(endPressure - startPressure, " hPa")} (higher pressure usually favors cold-air push).`,
      ],
      [
        'Precip / cloud',
        `${Math.round(maxPrecip)}% / ${Math.round(maxCloud)}% for cloud-suppression judgement around peak hours.`,
      ],
    ] as const;
  }

  const recent = Array.isArray(detail.metar_recent_obs)
    ? detail.metar_recent_obs.slice(-4)
    : [];
  const nearby = Array.isArray(detail.official_nearby) && detail.official_nearby.length
    ? detail.official_nearby
    : Array.isArray(detail.mgm_nearby)
      ? detail.mgm_nearby
      : [];
  const nearbySource = String(detail.nearby_source || "").toLowerCase();
  const sourceLabel =
    nearbySource === "mgm" || isTurkishMgmCity(detail)
      ? 'MGM nearby stations'
      : nearbySource === "official_cluster"
        ? 'Official nearby stations'
      : 'METAR nearby stations';
  const currentTemp = Number(detail.current?.temp);
  const recentTemps = recent
    .map((point) => Number(point.temp))
    .filter((value) => Number.isFinite(value));
  const baseline = recentTemps.length ? recentTemps[0] : currentTemp;
  const shortDelta =
    Number.isFinite(currentTemp) && Number.isFinite(baseline)
      ? currentTemp - baseline
      : 0;
  let nearbyLead: { diff: number; name: string; temp: number; syncText: string } | null = null;

  for (const station of nearby) {
    const temp = Number(station.temp);
    if (station.usable_for_intraday === false) continue;
    if (!Number.isFinite(temp) || !Number.isFinite(currentTemp)) continue;
    const diff = temp - currentTemp;
    const syncStatus = String(station.sync_status || "").toLowerCase();
    const syncDelta = Number(station.time_delta_vs_anchor_minutes);
    const syncText =
      syncStatus === "synced"
        ? 'time-synced'
        : syncStatus === "near_realtime" || syncStatus === "lagged"
          ? Number.isFinite(syncDelta)
            ? `${Math.round(syncDelta)} min offset`
            : 'not fully synchronized'
          : syncStatus === "unknown"
            ? 'timing unverified'
            : "";
    if (!nearbyLead || Math.abs(diff) > Math.abs(nearbyLead.diff)) {
      nearbyLead = {
        diff,
        name:
          station.name ||
          station.icao ||
          ('Nearby station'),
        temp,
        syncText,
      };
    }
  }
  const usableNearbyCount = nearby.filter((station) => station.usable_for_intraday !== false).length;

  const rows: Array<readonly [string, string]> = [
    [
      'Primary station',
      `${detail.current?.temp ?? "--"}${detail.temp_symbol} @ ${detail.current?.obs_time || "--"}`,
    ],
    [
      'Raw METAR',
      detail.current?.raw_metar || ('N/A'),
    ],
    [
      'Next 0-2h',
      `${formatDelta(shortDelta, detail.temp_symbol)} based on latest METAR sequence short-term momentum.`,
    ],
    [
      sourceLabel,
      `${usableNearbyCount}/${nearby.length} stations usable for the nearby scan; station timestamps may differ.`,
    ],
  ];

  if (nearbyLead) {
    const tone = nearbyLead.diff > 0
        ? "warmer"
        : nearbyLead.diff < 0
          ? "cooler"
          : "flat";
    rows.push([
      'Leading station',
      `${nearbyLead.name} ${nearbyLead.temp}${detail.temp_symbol}, relative to primary station ${formatDelta(nearbyLead.diff, detail.temp_symbol)} (${tone})${nearbyLead.syncText ? `; ${nearbyLead.syncText}` : ""}.`,
    ]);
  }

  return rows;
}


function toFiniteNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function firstFiniteNumber(values: unknown[]) {
  for (const value of values) {
    const numeric = toFiniteNumber(value);
    if (numeric != null) return numeric;
  }
  return null;
}

function firstNonEmptyString(values: unknown[]) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return null;
}

function formatObservationUpdate(value: unknown, locale: Locale) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;

  const looksDated =
    /^\d{4}-\d{2}-\d{2}/.test(raw) ||
    raw.includes("T") ||
    /[+-]\d{2}:?\d{2}$/.test(raw);
  if (looksDated) {
    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
      return new Intl.DateTimeFormat("en-US", {
        day: "2-digit",
        hour: "2-digit",
        hour12: false,
        minute: "2-digit",
        month: "2-digit",
      })
        .format(parsed)
        .replace(",", "");
    }
  }

  return normalizeHm(raw) || raw;
}

function localObservationTimeCandidate(value: unknown) {
  const raw = String(value ?? "").trim();
  if (!raw || raw.includes("T") || /^\d{4}-\d{2}-\d{2}/.test(raw)) {
    return "";
  }
  return normalizeHm(raw) || raw;
}

function getOfficialObservationCandidates(detail: CityDetail) {
  const officialNearby = Array.isArray(detail.official_nearby)
    ? detail.official_nearby
    : [];
  const mgmNearby = Array.isArray(detail.mgm_nearby) ? detail.mgm_nearby : [];
  const officialAnchor =
    officialNearby.find(
      (station) => station.is_settlement_anchor || station.is_airport_station,
    ) || officialNearby[0];

  return {
    centerStation: detail.center_station_candidate,
    mgmNearby,
    officialAnchor,
    officialNearby,
  };
}

function getObservedTemperatureProfile(detail: CityDetail, locale: Locale) {
  const { mgmNearby } = getOfficialObservationCandidates(detail);
  const displayAirportPrimary = getDisplayAirportPrimary(detail);
  const currentSource = String(
    detail.current?.settlement_source ||
      detail.current?.settlement_source_label ||
      "",
  )
    .trim()
    .toLowerCase();
  const isNmcCurrent = currentSource === "nmc" || currentSource.includes("nmc");
  const isNmcAirport = String(displayAirportPrimary?.source_label || "")
    .trim()
    .toLowerCase()
    .includes("nmc");
  const candidates = [
    {
      sourceLabel: detail.airport_current?.source_label || "METAR",
      temp: detail.airport_current?.temp,
    },
    {
      sourceLabel: displayAirportPrimary?.source_label || "METAR",
      temp: isNmcAirport ? null : displayAirportPrimary?.temp,
    },
    {
      sourceLabel: detail.current?.settlement_source_label,
      temp: isNmcCurrent ? null : detail.current?.temp,
    },
    {
      sourceLabel: mgmNearby[0]?.source_label,
      temp: mgmNearby[0]?.temp,
    },
  ];
  const selected = candidates.find((candidate) =>
    Number.isFinite(Number(candidate.temp)),
  );

  if (!selected) {
    return 'Unavailable';
  }

  const observedTemp = Number(selected.temp);
  const sourceLabel = firstNonEmptyString([
    selected.sourceLabel,
    getObservationSourceTag(detail),
  ]);
  const rounded =
    Math.abs(observedTemp - Math.round(observedTemp)) < 0.05
      ? String(Math.round(observedTemp))
      : observedTemp.toFixed(1);

  return `${rounded}${detail.temp_symbol || "°C"}${
    sourceLabel ? ` · ${sourceLabel}` : ""
  }`;
}

function getObservationUpdateProfile(detail: CityDetail, locale: Locale) {
  const { mgmNearby } = getOfficialObservationCandidates(detail);
  const mgmFirstRecord = asRecord(mgmNearby[0]);
  const displayAirportPrimary = getDisplayAirportPrimary(detail);
  const currentSource = String(
    detail.current?.settlement_source ||
      detail.current?.settlement_source_label ||
      "",
  )
    .trim()
    .toLowerCase();
  const isNmcCurrent = currentSource === "nmc" || currentSource.includes("nmc");
  const rawValue = firstNonEmptyString([
    isNmcCurrent ? "" : detail.current?.obs_time,
    localObservationTimeCandidate(displayAirportPrimary?.obs_time),
    localObservationTimeCandidate(detail.airport_current?.obs_time),
    localObservationTimeCandidate(mgmFirstRecord?.obs_time),
    localObservationTimeCandidate(mgmFirstRecord?.time),
    displayAirportPrimary?.report_time,
    detail.airport_current?.report_time,
    isNmcCurrent ? "" : detail.current?.report_time,
    mgmFirstRecord?.report_time,
    detail.updated_at,
  ]);

  return (
    formatObservationUpdate(rawValue, locale) ||
    ('Unavailable')
  );
}

export function getCityProfileStats(detail: CityDetail, locale: Locale = "en-US") {
  const risk = detail.risk || {};
  const nearbyCount = Array.isArray(detail.mgm_nearby) ? detail.mgm_nearby.length : 0;
  const nearbySource = String(detail.nearby_source || "").trim().toLowerCase();
  const sourceCode = getObservationSourceCode(detail);
  const isOfficialSource =
    sourceCode === "hko" ||
    sourceCode === "cwa" ||
    sourceCode === "noaa";

  const sourceDisplay = (() => {
    if (sourceCode === "hko") {
      return 'Hong Kong Observatory (HKO)';
    }
    if (sourceCode === "cwa") {
      return 'Central Weather Administration (CWA)';
    }
    if (sourceCode === "noaa") {
      const noaaCode = getNoaaStationCode(detail);
      const noaaName = getNoaaStationName(detail);
      return `${noaaName}${noaaCode ? ` (${noaaCode})` : ""}`;
    }
    if (sourceCode === "wunderground") {
      const icao = String(detail.risk?.icao || detail.current?.station_code || "")
        .trim()
        .toUpperCase();
      const stationName = String(
        detail.current?.station_name || detail.risk?.airport || "",
      ).trim();
      return `${stationName || icao || "Airport"}${icao ? ` (${icao} METAR)` : " METAR"}`;
    }
    const stationName = String(
      detail.current?.station_name || detail.risk?.airport || "",
    ).trim();
    const stationCode = String(
      detail.current?.station_code || detail.risk?.icao || "",
    ).trim();
    if (stationName) {
      return `${stationName}${stationCode ? ` (${stationCode})` : ""}`;
    }
    const tag = getObservationSourceTag(detail);
    if (sourceCode === "mgm") {
      return isEnglish(locale) ? `MGM (${tag})` : `MGM (${tag})`;
    }
    if (risk.airport && risk.icao) return `${risk.airport} (${risk.icao})`;
    if (risk.airport) return String(risk.airport);
    return 'No profile';
  })();

  const rows = [
    {
      label: isOfficialSource
        ? 'Settlement station'
        : 'Settlement airport',
      value: sourceDisplay,
    },
    {
      label: isOfficialSource
        ? 'Reference distance'
        : 'Station distance',
      value:
        risk.distance_km != null && Number.isFinite(Number(risk.distance_km))
          ? `${risk.distance_km} km`
          : 'Not marked',
    },
    {
      label: 'Observed temp',
      value: getObservedTemperatureProfile(detail, locale),
    },
    {
      label: 'Observation update',
      value: getObservationUpdateProfile(detail, locale),
    },
    {
      label: 'Nearby stations',
      value:
        nearbyCount > 0
          ? `${nearbyCount} participating stations`
          : 'No nearby stations',
    },
  ];

  if (nearbyCount > 0) {
    rows.push({
      label: 'Nearby source',
      value:
        nearbySource === "kma"
          ? 'KMA official stations'
          : nearbySource === "official_cluster"
            ? 'Official station cluster'
            : nearbySource === "mgm"
              ? "MGM"
              : 'Airport / METAR network',
    });
  }

  if (nearbySource === "kma" && detail.airport_current?.temp != null) {
    const airportLabel =
      String(
        detail.airport_current.station_label ||
          detail.airport_current.station_code ||
          detail.risk?.airport ||
          "",
      ).trim() ||
      ('Airport station');
    const airportObsTime =
      String(detail.airport_current.obs_time || "").trim() ||
      ('pending');
    const airportHigh =
      detail.airport_current.max_so_far != null
        ? `${detail.airport_current.max_so_far}${detail.temp_symbol || ""}${
            detail.airport_current.max_temp_time
              ? ` @ ${detail.airport_current.max_temp_time}`
              : ""
          }`
        : 'Unavailable';

    rows.push({
      label: 'Airport reference',
      value: `${airportLabel}: ${detail.airport_current.temp}${
        detail.temp_symbol || ""
      } @ ${airportObsTime}`,
    });
    rows.push({
      label: 'Airport high',
      value: airportHigh,
    });
  }

  return rows;
}

export function getSettlementRiskNarrative(
  detail: CityDetail,
  locale: Locale = "en-US",
) {
  const risk = detail.risk || {};
  const sourceCode = getObservationSourceCode(detail);
  const stationTerm =
    sourceCode === "hko" ||
    sourceCode === "cwa" ||
    sourceCode === "noaa"
    ? 'settlement reference station'
    : 'settlement airport';
  const lines: string[] = [];

  if (risk.warning) {
    lines.push(
      `Current key risk: ${risk.warning}`,
    );
  }

  if (risk.distance_km != null) {
    if (risk.distance_km >= 60) {
      lines.push(
        `The ${stationTerm} is far from urban core; market feel and settlement value may diverge significantly.`,
      );
    } else if (risk.distance_km >= 25) {
      lines.push(
        `The ${stationTerm} has material distance from downtown; peak/overnight rhythm should prioritize the settlement station.`,
      );
    } else {
      lines.push(
        `The ${stationTerm} is close enough; city feel and settlement temperature are usually more synchronized.`,
      );
    }
  }

  if (isTurkishMgmCity(detail)) {
    lines.push(
      'For Turkish MGM-supported cities, focus on the airport station plus MGM nearby-station linkage, not urban sensation alone.',
    );
  }

  if (detail.current?.obs_age_min != null) {
    if (detail.current.obs_age_min >= 45) {
      lines.push(
        `Current METAR is ${detail.current.obs_age_min} minutes old. Blend nearby stations for nowcast instead of single-station snapshot.`,
      );
    } else {
      lines.push(
        'Primary station observation is fresh enough; short-term judgement can anchor on it.',
      );
    }
  }

  return lines;
}

export function getClimateDrivers(detail: CityDetail, locale: Locale = "en-US") {
  const drivers: Array<{ label: string; text: string }> = [];
  const lat = Math.abs(Number(detail.lat));
  const nearbyCount = Array.isArray(detail.mgm_nearby)
    ? detail.mgm_nearby.length
    : 0;
  const distanceKm = Number(detail.risk?.distance_km);

  if (lat >= 50) {
    drivers.push({
      label: 'High-latitude cold air',
      text: 'At higher latitude, temperature rhythm is more affected by cold-air surges, trough passage, and seasonal radiation angle.',
    });
  } else if (lat >= 35) {
    drivers.push({
      label: 'Mid-latitude westerlies',
      text: 'Temperature shifts are often controlled by frontal transitions rather than pure daytime radiation.',
    });
  } else if (lat >= 20) {
    drivers.push({
      label: 'Subtropical highs',
      text: 'Subtropical ridge, clear-sky radiation and low-level warm advection often dominate warming efficiency.',
    });
  } else {
    drivers.push({
      label: 'Tropical moisture & convection',
      text: 'Temperature and feels-like are often modulated by moisture transport, cloud convection and showers.',
    });
  }

  drivers.push({
    label: 'Dry-wet boundary layer',
    text: 'Boundary-layer humidity controls daytime warming efficiency; dry boundary warms faster, wet boundary is more cloud/precip-sensitive.',
  });

  drivers.push({
    label: 'Advection transport',
    text: 'Short-term trend is usually driven by low-level air-mass transport. Persistent wind origin tends to sustain thermal direction.',
  });

  if (Number.isFinite(distanceKm) && distanceKm >= 25) {
    drivers.push({
      label: 'Station representativeness',
      text: 'When settlement station is not near city core, perceived temperature and settlement value may diverge.',
    });
  }

  if (nearbyCount >= 4) {
    drivers.push({
      label: 'Local heterogeneity',
      text: 'More nearby stations suggest terrain/urban-heat heterogeneity; settlement station and downtown sensation should be evaluated separately.',
    });
  }

  return drivers;
}
