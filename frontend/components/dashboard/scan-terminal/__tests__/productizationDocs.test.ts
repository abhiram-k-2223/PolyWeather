import { getDocsPage } from "@/content/docs/docs";
import { DOCS_PAGES } from "@/content/docs/docs";
import { DOCS_GROUPS } from "@/content/docs/docs.config";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function pageText(slug: string) {
  const page = getDocsPage(slug);
  assert(page, `${slug} docs page should exist`);
  return [
    page.content.title,
    page.content.description,
    ...page.content.sections.flatMap((section) => [
      section.title,
      ...section.blocks.flatMap((block) => {
        if (block.type === "paragraph" || block.type === "callout") return [block.text];
        if (block.type === "bullets" || block.type === "steps") return block.items;
        if (block.type === "link") return [block.label, block.caption || ""];
        if (block.type === "image") return [block.alt, block.caption || ""];
        return [];
      }),
    ]),
  ].join("\n");
}

export function runTests() {
  const publicDocSlugs = DOCS_PAGES.map((page) => page.slug);
  assert(
    publicDocSlugs.join(",") === "intro,chart-guide,realtime-sources,settlement-sources,extension",
    "public docs navigation should only expose current shipped user-facing surfaces",
  );
  for (const group of DOCS_GROUPS) {
    assert(
      DOCS_PAGES.some((page) => page.group === group.id),
      `docs navigation group should not be empty: ${group.id}`,
    );
  }

  const allDocs = publicDocSlugs.map((slug) => pageText(slug)).join("\n");
  for (const staleTerm of ["map entry", "opportunity board", "calendar", "LGBM", "city decision card", "paid decision workspace", "refresh lock"]) {
    assert(!allDocs.includes(staleTerm), `public docs should not expose stale term: ${staleTerm}`);
  }

  const intro = pageText("intro");
  assert(
    intro.includes("settlement-source-first") && intro.includes("weather decision terminal"),
    "intro should position PolyWeather as a settlement-source-first decision terminal",
  );
  assert(
    intro.includes("1-9 chart slots") && intro.includes("Weather Decisions / Training / Guide"),
    "intro should match the current terminal navigation and chart-slot workflow",
  );

  const chartGuide = pageText("chart-guide");
  assert(chartGuide.includes("How to Read PolyWeather Charts"), "chart guide title should be present");
  assert(chartGuide.includes("Advanced weather variables") && chartGuide.includes("hidden by default"), "chart guide should explain advanced variables as hidden-by-default context");
  assert(chartGuide.includes("Do not read probability bands as observation curves"), "chart guide should warn against reading probability as observation");
  assert(chartGuide.includes("Peak / All Day"), "chart guide should document the current chart view modes");

  const realtimeSources = pageText("realtime-sources");
  assert(realtimeSources.includes("AMSC 180s"), "realtime sources should document the AMSC 180s cadence");
  assert(realtimeSources.includes("AMOS 60s"), "realtime sources should document the AMOS 60s cadence");
  assert(realtimeSources.includes("SSE patch"), "realtime sources should document the SSE patch path");

  const settlementSources = pageText("settlement-sources");
  assert(settlementSources.includes("Settlement Stations"), "settlement source guide should exist");
  assert(settlementSources.includes("airport METAR") && settlementSources.includes("official settlement station"), "settlement source guide should distinguish airport and official station settlement");

  assert(
    getDocsPage("chart-guide")?.group === "getting-started" &&
      getDocsPage("realtime-sources")?.group === "settlement" &&
      getDocsPage("settlement-sources")?.group === "settlement",
    "docs navigation should expose chart, realtime source, and settlement source guides in the right groups",
  );

  const chartGuideEn = pageText("chart-guide");
  assert(chartGuideEn.includes("How to Read PolyWeather Charts"), "chart guide should exist");
  assert(chartGuideEn.includes("hidden by default"), "chart guide should describe hidden-by-default advanced variables");
  assert(chartGuideEn.includes("Peak / All Day"), "chart guide should document the current chart view modes");
}
