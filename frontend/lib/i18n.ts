export type Locale = "en-US";

type MessageParams = Record<string, string | number>;

const DEFAULT_LOCALE: Locale = "en-US";
export const LOCALE_STORAGE_KEY = "polyweather.locale";

const MESSAGES: Record<Locale, Record<string, string>> = {
  "en-US": {
    "header.subtitle": "Weather Derivatives Intelligence",
    "header.home": "Home",
    "header.homeAria": "Return to the main map homepage",
    "header.docs": "Docs",
    "header.docsAria": "Open product documentation",
    "header.info": "Tech Notes",
    "header.infoAria": "Open system technical notes",
    "header.account": "Account",
    "header.accountAria": "Open account center",
    "header.signIn": "Sign In / Register",
    "header.signInAria": "Open sign-in page",
    "header.live": "LIVE",
    "header.refreshAria": "Refresh all data",
    "header.langAria": "Switch language",
    "header.langZh": "Chinese",
    "header.langEn": "EN",

    "sidebar.title": "Monitored Cities",
    "sidebar.currentTemp": "Current {temp}",
    "sidebar.peakTempAt": "Peak {temp} @ {time}",
    "sidebar.peakAt": "Peak @ {time}",
    "sidebar.group.high": "Recent Strong",
    "sidebar.group.medium": "Recent Mixed",
    "sidebar.group.low": "Recent Weak",
    "sidebar.group.other": "Low Sample",

    "dashboard.loading":
      "Synchronizing station observations and settlement station data...",
    "dashboard.opportunityTitle": "Tradable Opportunities",
    "dashboard.opportunitySubtitle":
      "Identify high-value markets based on real-time data, model predictions, and current time window.",
    "dashboard.opportunityList": "Opportunity List",
    "dashboard.distributionView": "Distribution",
    "dashboard.calendarView": "Calendar",

    "detail.closeAria": "Close city detail panel",
    "detail.waitSelect": "Waiting for city selection",
    "detail.todayAnalysis": "Today's Intraday",
    "detail.loading": "Loading city details...",
    "detail.emptyHint": "Select a city from the left list to view details.",
    "detail.sceneryAlt": "{city} scenery",
    "detail.sceneryTitle": "City Scenery & Microclimate",
    "detail.sceneryFallback":
      "No scenery image matched. You can still review station and observation profile below.",
    "detail.profile": "City Profile",
    "detail.todayMiniTrend": "Today's Intraday Trend (Compact)",
    "detail.chartLegendEmpty":
      "No hourly observations or forecast curve available.",
    "detail.currentTemp": "Current Temp",
    "detail.todayHigh": "Today's High",
    "detail.targetTemp": "Target Temp",
    "detail.gap": "Gap to Target",
    "detail.peakWindow": "Peak Window",
    "detail.compositeScore": "Composite Score",
    "detail.selectCity": "← Select a city from the list",
    "detail.localTime": "Local Time",

    "forecast.title": "Multi-day Forecast",
    "forecast.empty": "No multi-day forecast available",
    "forecast.today": "Today",

    "guide.title": "📎 PolyWeather Technical Overview",
    "guide.closeAria": "Close technical overview",
    "guide.footer":
      "Primary data sources are METAR, Hong Kong Observatory (HKO), designated NOAA stations, Turkish MGM, Open-Meteo, and weather.gov.",


    "future.todayTitle": "{city} · Intraday Analysis",
    "future.dateTitle": "{city} · {date} Future-date Analysis",
    "future.closeTodayAria": "Close intraday analysis",
    "future.closeDateAria": "Close future-date analysis",
    "future.currentObs": "Current Observation",
    "future.currentWeather": "Current Weather",
    "future.wuRef": "Settlement Ref",
    "future.sunrise": "Sunrise",
    "future.sunset": "Sunset",
    "future.sunshine": "Sunshine Duration",
    "future.todayForecastHigh": "Today's Forecast High",
    "future.targetForecast": "Target-day Forecast",
    "future.deb": "DEB Forecast",
    "future.mu": "Dynamic Distribution Center",
    "future.score": "Trend Score",
    "future.todayTempTrend": "Today's Temperature Trend",
    "future.targetTempTrend": "Target-day Hourly Trend",
    "future.probability": "Model Probability Reference",
    "future.models": "Multi-model Forecast",
    "future.structureToday": "Intraday Structural Signal",
    "future.structureDate": "6-48h Structural Trend",
    "future.judgement": "Judgement",
    "future.confidence": "Confidence",
    "future.maxPrecip": "Max Precip Probability",
    "future.ai": "Structured Observations",
    "future.noAi":
      "No structured observations are available. Model data is used as baseline.",
    "future.weatherGov": "weather.gov text",
    "future.risk": "Settlement & Deviation Risk",
    "future.climate": "What Mainly Drives Local Climate",
    "future.chartLegendEmpty":
      "No hourly observations available",

    "confidence.high": "High",
    "confidence.medium": "Medium",
    "confidence.low": "Low",

    "section.todayTempTrend": "Today's Temperature Trend",
    "section.chartEmpty": "No hourly data available",
    "section.probability": "Calibrated Model Probability",
    "section.mu": "Dynamic center μ = {value}{unit}",
    "section.noProb": "No probability data available",
    "section.models": "Multi-model Forecast",
    "section.noModels": "No multi-model forecast available",
    "section.ai": "Structured Observations",
    "section.aiEmpty":
      "No structured observations are available. Model data is currently used.",
    "section.risk": "Data Deviation Risk",
    "section.noRiskProfile": "No risk profile available",
    "section.airport": "Airport",
    "section.distance": "Distance",
    "section.note": "Note",
    "section.currentConditions": "Current Conditions",
    "section.tradeRecommendation": "Recommendation",

    "filter.mode": "Scan Mode",
    "filter.tradable": "Tradable",
    "filter.early": "Early Stage",
    "filter.touch": "Touch Play",
    "filter.trend": "Trend Confirm",
    "filter.conditions": "Conditions",
    "filter.priceRange": "Price Range",
    "filter.edgeRange": "Min Edge",
    "filter.onlyTradable": "Only Tradable",
    "filter.liquidity": "Liquidity",
    "filter.liquidityHigh": "High Liquidity",
    "filter.timeRange": "Time Period",
    "filter.scanCta": "Start Scan",

    "account.title": "Account Center",
    "account.subtitle": "Review identity, access status, and bot binding info",
    "account.backDashboard": "Back to Dashboard",
    "account.refresh": "Refresh",
    "account.signIn": "Sign in / Sign up",
    "account.signOut": "Sign out",
    "account.loading": "Syncing account snapshot...",
    "account.error": "Failed to load: {message}",
    "account.updatedAt": "Last synced: {time}",
    "account.guestName": "PolyWeather User",
    "account.authenticated": "Authenticated",
    "account.guest": "Guest Mode",
    "account.subscriptionActive": "Subscription active",
    "account.subscriptionRequired": "Subscription required",
    "account.subscriptionUnknown": "Subscription unknown",
    "account.card.membership": "Membership & Access",
    "account.card.identity": "Identity",
    "account.card.backend": "Backend Auth",
    "account.card.bot": "Bot Binding",
    "account.field.email": "Email",
    "account.field.userId": "User ID",
    "account.field.provider": "Sign-in method",
    "account.field.lastSignIn": "Last sign-in",
    "account.field.mode": "Entitlement mode",
    "account.field.backendStatus": "Backend status",
    "account.field.subscription": "Subscription result",
    "account.field.requirement": "Subscription policy",
    "account.field.bindCommand": "Binding command",
    "account.field.bindHint":
      "Send this command to the Telegram bot to bind web account identity.",
    "account.mode.supabaseRequired": "Supabase required auth",
    "account.mode.supabaseOptional": "Supabase optional auth",
    "account.mode.supabase": "Supabase session auth",
    "account.mode.legacy": "Legacy token auth",
    "account.mode.disabled": "Auth guard disabled",
    "account.mode.unknown": "Unknown mode",
    "account.backend.ok": "Passed",
    "account.backend.fail": "Failed",
    "account.subscription.active": "Active",
    "account.subscription.inactive": "Inactive/Expired",
    "account.subscription.notRequired": "Not required now",
    "account.subscription.unknown": "Unknown",
    "account.copy": "Copy",
    "account.copied": "Copied",
    "account.na": "--",

    "common.na": "--",
  },
};

export function normalizeLocale(value?: string | null): Locale {
  return "en-US";
}

export function getInitialLocaleFromNavigator(): Locale {
  return "en-US";
}

export function formatMessage(
  key: string,
  params?: MessageParams,
): string {
  const template = MESSAGES["en-US"]?.[key] || key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, token) => {
    const value = params[token];
    return value == null ? "" : String(value);
  });
}
