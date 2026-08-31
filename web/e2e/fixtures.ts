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
    metadata: {
      generated_utc: "2026-08-31T00:00:00Z",
      method:
        "circle with the same area as the routed 10-minute isochrone (Valhalla, free-flow)",
      center: { lat, lon, name },
      radius_m: 4_200,
      isochrone_area_km2: 55.42,
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
        "named facilities inside the displayed equal-area circle derived from the routed 10-minute drive isochrone, not the isochrone geometry itself",
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
  facilities: facilities(833, "Bundled OpenStreetMap fixture"),
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
    boundary_mode: "routed_equal_area_circle",
    warnings:
      status === "osm_only"
        ? ["Overture enrichment failed in this deterministic test fixture."]
        : [],
    enrich_error: status === "osm_only" ? true : undefined,
  };
}
