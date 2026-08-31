import type {
  AreaResponse,
  AreaStatus,
  DefaultViewData,
  FacilitiesCollection,
  FacilityCategoryKey,
  GeocodeCandidate,
} from "../api/types";

const categoryKeys: FacilityCategoryKey[] = [
  "dining",
  "health",
  "education",
  "lodging",
  "shopping",
  "fuel_ev",
  "culture",
  "parks",
];

export function candidate(
  name: string,
  lat = 37.4275,
  lon = -122.1697,
): GeocodeCandidate {
  return {
    name,
    display_name: `${name}, California`,
    lat,
    lon,
    osm: `node/${name.toLowerCase().replaceAll(" ", "-")}`,
  };
}

export function facilities(total = 0): FacilitiesCollection {
  return {
    metadata: {
      generated_utc: "2026-08-30T00:00:00Z",
      source: "OpenStreetMap test fixture",
      filter: "named facilities inside the test boundary",
    },
    categories: Object.fromEntries(
      categoryKeys.map((key, index) => [
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

export function areaResponse(
  status: AreaStatus,
  place: GeocodeCandidate,
  total = status === "complete" ? 42 : 12,
): AreaResponse {
  return {
    status,
    name: place.name,
    lat: place.lat,
    lon: place.lon,
    boundary: {
      type: "FeatureCollection",
      metadata: {
        generated_utc: "2026-08-30T00:00:00Z",
        method: "circle with the same area as the routed test isochrone",
        center: { lat: place.lat, lon: place.lon, name: place.name },
        radius_m: 5_000,
      },
      features: [
        {
          type: "Feature",
          properties: { contour: "approx 10 min drive" },
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [place.lon - 0.01, place.lat - 0.01],
                [place.lon + 0.01, place.lat - 0.01],
                [place.lon + 0.01, place.lat + 0.01],
                [place.lon - 0.01, place.lat + 0.01],
                [place.lon - 0.01, place.lat - 0.01],
              ],
            ],
          },
        },
      ],
    },
    facilities: facilities(total),
    total,
    enrich_error: status === "osm_only" ? true : undefined,
  };
}

export function defaultViewData(): DefaultViewData {
  const applePark = candidate("Apple Park", 37.33484, -122.01139);
  const response = areaResponse("complete", applePark, 833);
  return {
    boundary: response.boundary,
    facilities: response.facilities,
    landmarks: [],
  };
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}
