import type {
  AreaResponse,
  AreaStatus,
  BoundaryFeatureCollection,
  DefaultViewData,
  FacilitiesCollection,
  FacilityCategoryKey,
  GeocodeCandidate,
} from "../src/api/types";

export type { AreaStatus } from "../src/api/types";

const CATEGORY_KEYS = [
  "dining",
  "health",
  "education",
  "lodging",
  "shopping",
  "fuel_ev",
  "culture",
  "parks",
] as const satisfies readonly FacilityCategoryKey[];

export const STANFORD: GeocodeCandidate = {
  name: "Stanford University",
  display_name: "Stanford University, Santa Clara County, California",
  lat: 37.4275,
  lon: -122.1697,
  osm: "relation/2834628",
};

function boundary(
  name: string,
  lat: number,
  lon: number,
): BoundaryFeatureCollection {
  return {
    type: "FeatureCollection",
    boundary_mode: "routed_isochrone",
    metadata: {
      generated_utc: "2026-08-31T00:00:00Z",
      method:
        "routed 10-minute drive isochrone (Valhalla, auto costing, free-flow, denoise=0.3), rendered and filtered as returned; no circle approximation",
      denoise: 0.3,
      center: { lat, lon, name },
      isochrone_area_km2: 55.42,
      geometry_type: "Polygon",
      geometry_components: 1,
      geometry_holes: 0,
    },
    features: [
      {
        type: "Feature",
        properties: { contour: "approx 10 min drive" },
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [lon - 0.02, lat - 0.02],
              [lon + 0.02, lat - 0.02],
              [lon + 0.02, lat + 0.02],
              [lon - 0.02, lat + 0.02],
              [lon - 0.02, lat - 0.02],
            ],
          ],
        },
      },
    ],
  };
}

function facilities(total: number, source: string): FacilitiesCollection {
  return {
    metadata: {
      generated_utc: "2026-08-31T00:00:01Z",
      source,
      filter:
        "named facilities inside the displayed routed 10-minute drive isochrone (Valhalla, free-flow); the displayed geometry itself is the filter",
      overture_release: source.includes("Overture") ? "2026-08-19.0" : undefined,
    },
    categories: Object.fromEntries(
      CATEGORY_KEYS.map((key, index) => [
        key,
        {
          label_zh: key,
          label_en: key,
          color: "#2563eb",
          count: index === 0 ? total : 0,
          items: [],
        },
      ]),
    ) as unknown as FacilitiesCollection["categories"],
  };
}

export const DEFAULT_DATA: DefaultViewData = {
  boundary: boundary("Apple Park", 37.33484, -122.01139),
  facilities: facilities(921, "Bundled OpenStreetMap fixture"),
  landmarks: [],
};

export function areaFixture(status: AreaStatus): AreaResponse {
  const total = status === "complete" ? 42 : 12;
  return {
    status,
    name: STANFORD.name,
    lat: STANFORD.lat,
    lon: STANFORD.lon,
    boundary: boundary(STANFORD.name, STANFORD.lat, STANFORD.lon),
    facilities: facilities(
      total,
      status === "complete"
        ? "OpenStreetMap and Overture test fixture"
        : "OpenStreetMap test fixture",
    ),
    total,
    boundary_mode: "routed_isochrone",
    warnings:
      status === "osm_only"
        ? ["Overture enrichment failed in this deterministic test fixture."]
        : [],
    enrich_error: status === "osm_only" ? true : undefined,
  };
}
