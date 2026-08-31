import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  areaFixture,
  DEFAULT_DATA,
  STANFORD,
  type AreaStatus,
} from "./fixtures";

type TerminalStatus = "complete" | "osm_only";
type PublicUpstream = "nominatim" | "valhalla" | "overpass";

interface ScenarioCalls {
  data: string[];
  geocode: URL[];
  area: URL[];
  areaStatuses: AreaStatus[];
}

interface NetworkGuard {
  publicUpstream: Record<PublicUpstream, string[]>;
  osmTilesIntercepted: string[];
  unexpectedExternal: string[];
}

const APP_ORIGIN = "http://127.0.0.1:4173";

function upstreamFor(hostname: string): PublicUpstream | null {
  if (hostname.includes("nominatim")) return "nominatim";
  if (hostname.includes("valhalla")) return "valhalla";
  if (hostname.includes("overpass")) return "overpass";
  return null;
}

async function installNetworkGuard(
  context: BrowserContext,
): Promise<NetworkGuard> {
  const guard: NetworkGuard = {
    publicUpstream: {
      nominatim: [],
      valhalla: [],
      overpass: [],
    },
    osmTilesIntercepted: [],
    unexpectedExternal: [],
  };

  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === APP_ORIGIN) {
      await route.continue();
      return;
    }

    if (url.hostname === "tile.openstreetmap.org") {
      guard.osmTilesIntercepted.push(url.href);
      await route.fulfill({ status: 204, contentType: "image/png" });
      return;
    }

    const upstream = upstreamFor(url.hostname);
    if (upstream) {
      guard.publicUpstream[upstream].push(url.href);
    } else {
      guard.unexpectedExternal.push(url.href);
    }
    await route.abort("blockedbyclient");
  });

  return guard;
}

async function installScenarioRoutes(
  page: Page,
  terminalStatus: TerminalStatus,
): Promise<ScenarioCalls> {
  const calls: ScenarioCalls = {
    data: [],
    geocode: [],
    area: [],
    areaStatuses: [],
  };

  const dataFixtures = new Map<string, unknown>([
    ["/data/boundary.json", DEFAULT_DATA.boundary],
    ["/data/facilities.json", DEFAULT_DATA.facilities],
    ["/data/landmarks.json", DEFAULT_DATA.landmarks],
  ]);

  await page.route("**/data/*.json", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    calls.data.push(pathname);
    const body = dataFixtures.get(pathname);
    if (body === undefined) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Unknown test data path" }),
      });
      return;
    }
    await route.fulfill({ json: body });
  });

  await page.route("**/api/geocode?*", async (route) => {
    calls.geocode.push(new URL(route.request().url()));
    await route.fulfill({ json: { candidates: [STANFORD] } });
  });

  await page.route("**/api/area?*", async (route) => {
    calls.area.push(new URL(route.request().url()));
    const status: AreaStatus =
      calls.area.length === 1 ? "enriching" : terminalStatus;
    calls.areaStatuses.push(status);
    await route.fulfill({ json: areaFixture(status) });
  });

  return calls;
}

function expectNoPublicUpstream(guard: NetworkGuard) {
  expect(guard.publicUpstream.nominatim, "Nominatim requests").toEqual([]);
  expect(guard.publicUpstream.valhalla, "Valhalla requests").toEqual([]);
  expect(guard.publicUpstream.overpass, "Overpass requests").toEqual([]);
  expect(guard.unexpectedExternal, "unapproved external requests").toEqual([]);
  expect(
    guard.osmTilesIntercepted.length,
    "OSM tile requests should be fulfilled locally by the E2E guard",
  ).toBeGreaterThan(0);
}

async function runAreaScenario(
  page: Page,
  calls: ScenarioCalls,
  terminalStatus: TerminalStatus,
) {
  await page.goto("/");

  await expect(page.locator("[data-workflow-state]"))
    .toHaveAttribute("data-workflow-state", "idle");
  await expect(page.getByRole("heading", { name: "Apple Park" })).toBeVisible();
  expect(calls.geocode).toHaveLength(0);
  expect(calls.area).toHaveLength(0);

  await page.getByLabel("Destination or attraction").fill("Stanford");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page
    .getByRole("button", { name: /Stanford University/ })
    .click();

  await expect(page.locator("[data-workflow-state]"))
    .toHaveAttribute("data-workflow-state", "enriching");
  await expect(page.getByText(/OSM facilities are visible now/)).toBeVisible();

  const terminalDomState = terminalStatus === "complete" ? "complete" : "osmOnly";
  await expect(page.locator("[data-workflow-state]"))
    .toHaveAttribute("data-workflow-state", terminalDomState, { timeout: 10_000 });

  if (terminalStatus === "complete") {
    await expect(page.getByText(/Facilities complete \(42\)/)).toBeVisible();
    await expect(page.getByText("Complete", { exact: true })).toBeVisible();
  } else {
    await expect(page.locator(".status-card__message")).toContainText(
      "当前为 OSM-only 结果 · Overture enrichment failed",
    );
    await expect(
      page.getByText(
        "Overture enrichment failed in this deterministic test fixture.",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.getByText("OSM-only", { exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  }

  expect(calls.data.sort()).toEqual([
    "/data/boundary.json",
    "/data/facilities.json",
    "/data/landmarks.json",
  ]);
  expect(calls.geocode).toHaveLength(1);
  expect(calls.geocode[0]?.searchParams.get("q")).toBe("Stanford");
  expect(calls.area).toHaveLength(2);
  expect(calls.area[0]?.searchParams.get("lat")).toBe("37.4275");
  expect(calls.area[0]?.searchParams.get("lon")).toBe("-122.1697");
  expect(calls.area[0]?.searchParams.get("name")).toBe(STANFORD.name);
  expect(calls.area[1]?.href).toBe(calls.area[0]?.href);
  expect(calls.areaStatuses).toEqual(["enriching", terminalStatus]);
}

test.describe("intercepted area workflow", () => {
  test("renders enriching → complete without calling a public upstream", async ({
    context,
    page,
  }) => {
    const guard = await installNetworkGuard(context);
    const calls = await installScenarioRoutes(page, "complete");

    await runAreaScenario(page, calls, "complete");
    await expect.poll(() => guard.osmTilesIntercepted.length).toBeGreaterThan(0);
    expectNoPublicUpstream(guard);
  });

  test("renders enriching → osm_only with enrich_error=true", async ({
    context,
    page,
  }) => {
    const guard = await installNetworkGuard(context);
    const calls = await installScenarioRoutes(page, "osm_only");

    await runAreaScenario(page, calls, "osm_only");
    await expect.poll(() => guard.osmTilesIntercepted.length).toBeGreaterThan(0);
    expectNoPublicUpstream(guard);
  });
});
