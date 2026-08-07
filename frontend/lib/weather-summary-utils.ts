import type { CityDetail } from "@/lib/dashboard-types";
import type { Locale } from "@/lib/i18n";
import {
  getRealtimeObservationTag,
  isTurkishMgmCity,
} from "@/lib/observation-source-utils";

const METAR_WX_MAP: Record<
  string,
  { en: string; icon: string }
> = {
  VCSH: { en: "Showers nearby", icon: "🌦️" },
  SHRA: { en: "Rain showers", icon: "🌦️" },
  "-SHRA": { en: "Light rain showers", icon: "🌦️" },
  "+SHRA": { en: "Heavy rain showers", icon: "⛈️" },
  VCRA: { en: "Rain nearby", icon: "🌧️" },
  TSRA: { en: "Thunderstorms with rain", icon: "⛈️" },
  "-TSRA": { en: "Light thunderstorms with rain", icon: "⛈️" },
  "+TSRA": { en: "Heavy thunderstorms with rain", icon: "⛈️" },
  RA: { en: "Rain", icon: "🌧️" },
  "-RA": { en: "Light rain", icon: "🌦️" },
  "+RA": { en: "Heavy rain", icon: "⛈️" },
  SN: { en: "Snow", icon: "❄️" },
  "-SN": { en: "Light snow", icon: "🌨️" },
  "+SN": { en: "Heavy snow", icon: "🌨️" },
  DZ: { en: "Drizzle", icon: "🌦️" },
  FG: { en: "Fog", icon: "🌫️" },
  VCFG: { en: "Fog nearby", icon: "🌫️" },
  MIFG: { en: "Shallow fog", icon: "🌫️" },
  BR: { en: "Mist", icon: "🌫️" },
  HZ: { en: "Haze", icon: "🌫️" },
  TS: { en: "Thunderstorm", icon: "⛈️" },
  VCTS: { en: "Nearby thunderstorm", icon: "⛈️" },
  SQ: { en: "Squall", icon: "💨" },
  GS: { en: "Hail", icon: "🌨️" },
};

function isEnglish(locale: Locale) {
  return locale === "en-US";
}

function normalizeCloudSummary(
  cloudDesc: string | null | undefined,
  locale: Locale,
): { icon: string; text: string } {
  const raw = String(cloudDesc || "").trim();
  if (!raw) {
    return { icon: "🔍", text: 'Unknown' };
  }

  const lower = raw.toLowerCase();
  if (
    raw.includes("晴") ||
    raw.includes("晴朗") ||
    lower.includes("clear") ||
    lower.includes("sunny")
  ) {
    return { icon: "☀️", text: 'Clear' };
  }
  if (raw.includes("阴") || lower.includes("overcast")) {
    return { icon: "☁️", text: 'Overcast' };
  }
  if (raw.includes("多云") || lower.includes("cloud")) {
    return { icon: "☁️", text: 'Cloudy' };
  }
  if (raw.includes("少云") || lower.includes("few")) {
    return { icon: "🌤️", text: 'Mostly clear' };
  }
  if (raw.includes("散云") || lower.includes("scattered")) {
    return { icon: "⛅", text: 'Partly cloudy' };
  }
  return { icon: "🔍", text: raw };
}

export function translateMetar(code?: string | null, locale: Locale = "en-US") {
  if (!code) return null;
  const metarCode = String(code);
  for (const [key, value] of Object.entries(METAR_WX_MAP)) {
    if (metarCode.includes(key)) {
      return {
        icon: value.icon,
        label: value.en,
      };
    }
  }
  return { icon: "🔍", label: metarCode };
}

export function getRiskBadgeLabel(
  level?: string | null,
  locale: Locale = "en-US",
) {
  return (
    {
      high: "🔴 High Risk",
      low: "🟢 Low Risk",
      medium: "🟠 Medium Risk",
    }[String(level || "low")] || "Unknown Risk"
  );
}

export function getWeatherSummary(detail: CityDetail, locale: Locale = "en-US") {
  const current = detail.current || {};
  const cloud = normalizeCloudSummary(current.cloud_desc, locale);
  let weatherText = cloud.text;
  let weatherIcon = cloud.icon;

  if (current.wx_desc) {
    const translated = translateMetar(current.wx_desc, locale);
    if (translated) {
      weatherText = translated.label;
      weatherIcon = translated.icon;
    }
  }

  return { weatherIcon, weatherText };
}

export function getHeroMetaItems(detail: CityDetail, locale: Locale = "en-US") {
  const current = detail.current || {};
  const parts: string[] = [];
  const sourceTag = getRealtimeObservationTag(detail);
  const suppressAnkaraMgmObservation = isTurkishMgmCity(detail);

  if (current.obs_time) {
    const ageText =
      current.obs_age_min != null && current.obs_age_min >= 30
        ? ` (${current.obs_age_min} min ago)`
        : "";
    parts.push(`✈️ ${sourceTag} ${current.obs_time}${ageText}`);
  }

  if (current.wx_desc) {
    const translated = translateMetar(current.wx_desc, locale);
    if (translated) {
      parts.push(`${translated.icon} ${translated.label}`);
    }
  } else if (current.cloud_desc) {
    const cloud = normalizeCloudSummary(current.cloud_desc, locale);
    parts.push(`${cloud.icon} ${cloud.text}`);
  }

  if (current.wind_speed_kt != null) {
    parts.push(`💨 ${current.wind_speed_kt}kt`);
  }

  if (current.visibility_mi != null) {
    parts.push(`👁️ ${current.visibility_mi}mi`);
  }

  if (!suppressAnkaraMgmObservation && detail.mgm?.temp != null) {
    const timeMatch = detail.mgm.time?.match(/T?(\d{2}:\d{2})/);
    const timeText = timeMatch ? ` @${timeMatch[1]}` : "";
    parts.push(
      `🛰 MGM Obs: ${detail.mgm.temp}${detail.temp_symbol}${timeText}`,
    );
  }

  const trend = detail.trend || {};
  if (trend.is_dead_market) {
    parts.push('☠️ Flat market');
  } else if (trend.direction && trend.direction !== "unknown") {
    const labels: Record<string, string> = {
      falling: "📉 Cooling",
      mixed: "📊 Choppy",
      rising: "📈 Warming",
      stagnant: "⏸ Flat",
    };
    parts.push(labels[trend.direction] || trend.direction);
  }

  return parts;
}
