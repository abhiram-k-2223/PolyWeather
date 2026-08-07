export type DocsLocale = "en-US";

export type DocsBlock =
  | { type: "paragraph"; text: string }
  | { type: "callout"; tone?: "info" | "warning" | "success"; title?: string; text: string }
  | { type: "bullets"; items: string[] }
  | { type: "steps"; items: string[] }
  | { type: "link"; href: string; label: string; caption?: string }
  | { type: "image"; src: string; alt: string; caption?: string };

export interface DocsSection {
  id: string;
  title: string;
  blocks: DocsBlock[];
}

export interface DocsPageContent {
  title: string;
  description: string;
  sections: DocsSection[];
}

export interface DocsPageMeta {
  slug: string;
  group: "getting-started" | "analysis" | "settlement";
}

export interface DocsPage extends DocsPageMeta {
  content: DocsPageContent;
}

export interface DocsNavGroup {
  id: DocsPageMeta["group"];
  title: string;
}

export const DOCS_PAGES: DocsPage[] = [
  {
    slug: "intro",
    group: "getting-started",
    content: {
        title: "Introduction",
        description: "PolyWeather is a settlement-source-first weather decision terminal for reading live observations, the DEB path, market signals, and settlement rules.",
        sections: [
          {
            id: "what-is-polyweather",
            title: "What PolyWeather is",
            blocks: [
              {
                type: "paragraph",
                text: "PolyWeather is not a general weather app or a raw forecast page. It puts settlement runways, official stations, airport reports, DEB Forecast, market thresholds, and source freshness into one workflow for intraday temperature-market decisions.",
              },
              {
                type: "callout",
                tone: "info",
                title: "Product focus",
                text: "Confirm the settlement source and live update state first, then compare DEB with market signals. Market price is a decision layer, not a replacement for the actual settlement station or runway observation.",
              },
            ],
          },
          {
            id: "current-terminal",
            title: "What the current terminal exposes",
            blocks: [
              {
                type: "bullets",
                items: [
                  "Weather Decisions / Training / Guide: the three entries in the current left-side terminal navigation.",
                  "1-9 chart slots: use 1x1 through 3x3 layouts to monitor several cities and switch each slot independently.",
                  "Live anchors: settlement runway, official station, or useful airport report, with current temperature, day high, and freshness.",
                  "DEB Forecast: the orange path for remaining upside or downside and for checking the peak window.",
                  "Market signals: thresholds, price, liquidity, and edge support trade decisions after the weather evidence is checked.",
                  "Training data: recent DEB and probability-engine performance for judging which cities are more reliable now.",
                ],
              },
            ],
          },
          {
            id: "quick-read",
            title: "How to read the terminal quickly",
            blocks: [
              {
                type: "steps",
                items: [
                  "Pick a region and city, then place the important cities into chart slots.",
                  "Start with the teal live anchor: current temperature, day high, and freshness.",
                  "Compare DEB Forecast in Peak / All Day views to judge remaining move and peak-window risk.",
                  "Use model lines, runway detail, or market signals last to separate weather disagreement from price disagreement.",
                ],
              },
            ],
          },
        ],
    },
  },
  {
    slug: "chart-guide",
    group: "getting-started",
    content: {
        title: "How to Read PolyWeather Charts",
        description: "A guide to the current temperature chart reading order, layers, view modes, and common misreads.",
        sections: [
          {
            id: "read-order",
            title: "Read order",
            blocks: [
              {
                type: "steps",
                items: [
                  "Start with the settlement-source observation: settlement runway, official station, or the most useful airport report.",
                  "Read DEB Forecast next. It is the model-and-intraday adjusted path, not an observation that already happened.",
                  "Switch Peak / All Day views: Peak focuses the payoff window, while All Day reviews the full intraday path.",
                  "Use layers and market signals last, mainly when live observations diverge from DEB or price.",
                ],
              },
            ],
          },
          {
            id: "layers",
            title: "Chart layers",
            blocks: [
              {
                type: "bullets",
                items: [
                  "Live / settlement line: visible by default and closest to the settlement rule.",
                  "DEB Forecast: orange forecast path; focus on its gap versus live observations near the peak window.",
                  "Airport reports: METAR / MGM are airport references and are auto-shown only where useful.",
                  "Model lines: ECMWF, GFS, ICON, GEM, and related layers provide background context.",
                  "Runway detail: enable it to inspect runway sensors; disabling it still keeps the settlement runway or primary reference.",
                ],
              },
            ],
          },
          {
            id: "advanced-variables",
            title: "Advanced weather variables",
            blocks: [
              {
                type: "paragraph",
                text: "Wind speed, wind direction, dew point, humidity, and pressure help explain suppression, sea-breeze timing, cloud/rain risk, and boundary-layer structure, but they are not settlement-temperature curves.",
              },
              {
                type: "callout",
                tone: "info",
                title: "Hidden by default",
                text: "Advanced variables stay hidden by default and appear only as context when structure needs explanation, so the main chart is not crowded by non-temperature lines.",
              },
            ],
          },
          {
            id: "common-misreads",
            title: "Common misreads",
            blocks: [
              {
                type: "bullets",
                items: [
                  "Do not read probability bands as observation curves. Probability supports market analysis and background scoring, not timestamped live temperature.",
                  "Do not treat market signals as settlement temperature. Settlement still comes from the station or runway named by the rule.",
                  "Do not expect every city to update every minute. Refresh cadence follows source-native frequency and current data availability.",
                ],
              },
            ],
          },
        ],
    },
  },
  {
    slug: "realtime-sources",
    group: "settlement",
    content: {
        title: "Realtime Source Cadence",
        description: "Why cities update at different speeds, and how the website, cache, and SSE patches relate to each other.",
        sections: [
          {
            id: "source-cadence",
            title: "Source-native cadence",
            blocks: [
              {
                type: "bullets",
                items: [
                  "AMOS 60s: Korean runway sensors such as Seoul and Busan.",
                  "AMSC 180s: mainland China runway endpoint observations.",
                  "MADIS 300s: US high-frequency airport observations.",
                  "CoWIN 60s: Hong Kong 6087 reference station.",
                  "HKO 600s: Hong Kong Observatory official 10-minute layer.",
                  "CWA / JMA / FMI / KNMI / MGM: collected at each source's official or available cadence.",
                ],
              },
            ],
          },
          {
            id: "pipeline",
            title: "How the site updates",
            blocks: [
              {
                type: "paragraph",
                text: "The observation collector samples each source at its native cadence and writes cache or database state. The frontend reads a full snapshot first, then merges city_observation_patch.v1 updates through SSE patch.",
              },
              {
                type: "callout",
                tone: "info",
                title: "SSE patch",
                text: "Visible charts subscribe to live patches. If patches stop for a while, the chart can make a lightweight fallback refresh instead of making every entry point force-refresh the same external source.",
              },
            ],
          },
          {
            id: "telegram",
            title: "How Telegram relates",
            blocks: [
              {
                type: "paragraph",
                text: "Telegram reads the latest cache or database state by default and does not force-refresh observation sources. It only falls back to analysis when no cache exists at all.",
              },
            ],
          },
        ],
    },
  },
  {
    slug: "settlement-sources",
    group: "settlement",
    content: {
        title: "Settlement Stations",
        description: "Settlement rules differ by market. Confirm the station first, then read DEB, observations, and market signals.",
        sections: [
          {
            id: "why-settlement-matters",
            title: "Why the settlement station comes first",
            blocks: [
              {
                type: "paragraph",
                text: "A city-high market may settle from airport METAR, an airport primary site, a settlement runway, or an explicitly named official settlement station. A hotter downtown feel does not automatically mean the contract settles into a warmer bucket.",
              },
            ],
          },
          {
            id: "primary-rules",
            title: "Current primary rules",
            blocks: [
              {
                type: "bullets",
                items: [
                  "Most airport-linked markets: start with airport METAR, the airport primary site, or the project's marked airport reference.",
                  "Runway cities: prioritize the settlement runway or marked primary runway endpoint, while auxiliary runways remain spatial context.",
                  "Explicit official-station markets: settle from the official settlement station named by the rule, not from generic airport logic.",
                  "Local official enhancement layers: JMA, KMA, NMC, HKO, CWA, MGM, and similar sources help with lead/lag and cross-checks; whether they anchor settlement depends on the contract rule.",
                  "TAF: useful for cloud, thunderstorm, or wind-shift risk near the airport, but not the settlement temperature itself.",
                ],
              },
            ],
          },
          {
            id: "how-to-check",
            title: "How to check on the page",
            blocks: [
              {
                type: "steps",
                items: [
                  "Read the live / settlement line name and summary stats to identify the current anchor.",
                  "Check timestamp and freshness so stale sources do not drive the decision.",
                  "When runway detail is enabled, read the settlement runway first and use auxiliary runways for spatial spread.",
                  "Only then add DEB, market signals, and probability context.",
                ],
              },
            ],
          },
        ],
    },
  },
  {
    slug: "extension",
    group: "getting-started",
    content: {
        title: "Browser Extension",
        description: "PolyWeather Side Panel is a browser side-panel tool for quick city detection, compact chart context, and returning to the full weather decision terminal.",
        sections: [
          {
            id: "extension-install",
            title: "Install link",
            blocks: [
              {
                type: "link",
                href: "https://chromewebstore.google.com/detail/mhndjbgjljjfcfkojhmhpfcbconnikne?utm_source=item-share-cb",
                label: "Open Chrome Web Store",
                caption: "After installation, the side panel can show compact city context and route back to the main site.",
              },
            ],
          },
          {
            id: "extension-role",
            title: "What the extension does",
            blocks: [
              {
                type: "bullets",
                items: [
                  "Auto-detects the current page city, with manual switching also available.",
                  "Shows a city profile with settlement station, station distance, observation timestamp, and nearby station count.",
                  "Shows a compact intraday chart with DEB against the airport primary or official reference station, including hoverable time and temperature.",
                  "Shows a compact multi-day high forecast, plus refresh and return-to-site actions.",
                ],
              },
            ],
          },
          {
            id: "extension-permission",
            title: "Permissions and privacy",
            blocks: [
              {
                type: "bullets",
                items: [
                  "`tabs`: used to inspect the active tab URL and match the current city.",
                  "`storage`: used for local configuration and local cache only.",
                  "`sidePanel`: used to render the browser side panel UI.",
                  "The extension does not require login, does not collect personally identifiable information, and does not upload browsing history. It only requests weather endpoints when rendering the side panel.",
                ],
              },
            ],
          },
          {
            id: "extension-boundary",
            title: "What it does not do",
            blocks: [
              {
                type: "paragraph",
                text: "The extension does not carry the full analysis experience or payment flow. Multi-chart monitoring, training data, entitlement state, and complete market context stay on the main site.",
              },
            ],
          },
        ],
      },
    },
];


export function getDocsPage(slug: string) {
  return DOCS_PAGES.find((page) => page.slug === slug) || null;
}
