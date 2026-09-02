import { render } from "@testing-library/react";
import L from "leaflet";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type {
  BoundaryFeatureCollection,
  BoundaryGeometry,
  BoundaryMode,
} from "../api/types";
import { facilities } from "../test/fixtures";
import { AreaMap, boundaryBounds, boundaryLayerKey } from "./AreaMap";

/** Two components; the first carries a hole. Around Apple Park. */
const MULTIPOLYGON_WITH_HOLE: BoundaryGeometry = {
  type: "MultiPolygon",
  coordinates: [
    [
      [
        [-122.03, 37.32],
        [-121.99, 37.32],
        [-121.99, 37.35],
        [-122.03, 37.35],
        [-122.03, 37.32],
      ],
      [
        [-122.02, 37.33],
        [-122.0, 37.33],
        [-122.0, 37.34],
        [-122.02, 37.34],
        [-122.02, 37.33],
      ],
    ],
    [
      [
        [-121.95, 37.36],
        [-121.93, 37.36],
        [-121.93, 37.38],
        [-121.95, 37.38],
        [-121.95, 37.36],
      ],
    ],
  ],
};

const POLYGON_WITH_HOLE: BoundaryGeometry = {
  type: "Polygon",
  coordinates: MULTIPOLYGON_WITH_HOLE.coordinates[0],
};

const CENTER = { lat: 37.35, lon: -121.98 };

function boundary(
  geometry: BoundaryGeometry,
  mode: BoundaryMode | undefined = "routed_isochrone",
  generated = "2026-09-01T00:00:00Z",
): BoundaryFeatureCollection {
  const components =
    geometry.type === "MultiPolygon" ? geometry.coordinates.length : 1;
  const rings =
    geometry.type === "MultiPolygon"
      ? geometry.coordinates
      : [geometry.coordinates];
  return {
    type: "FeatureCollection",
    boundary_mode: mode,
    metadata: {
      generated_utc: generated,
      method: "routed isochrone test fixture",
      center: { ...CENTER, name: "Test center" },
      isochrone_area_km2: 12.3,
      geometry_type: geometry.type,
      geometry_components: components,
      geometry_holes: rings.reduce((sum, r) => sum + r.length - 1, 0),
    },
    features: [
      {
        type: "Feature",
        properties: { contour: "approx 10 min drive" },
        geometry,
      },
    ],
  };
}

describe("boundary geometry handling", () => {
  it("keeps every MultiPolygon component and its holes in the Leaflet layer", () => {
    const layer = L.geoJSON(boundary(MULTIPOLYGON_WITH_HOLE));
    const [polygon] = layer.getLayers() as L.Polygon[];
    const rings = polygon.getLatLngs() as L.LatLng[][][];

    expect(layer.getLayers()).toHaveLength(1);
    expect(rings).toHaveLength(2);
    expect(rings[0]).toHaveLength(2);
    expect(rings[1]).toHaveLength(1);
  });

  it("keeps the hole of a Polygon boundary", () => {
    const layer = L.geoJSON(boundary(POLYGON_WITH_HOLE));
    const [polygon] = layer.getLayers() as L.Polygon[];
    const rings = polygon.getLatLngs() as L.LatLng[][];

    expect(rings).toHaveLength(2);
    expect(rings[1]).toHaveLength(4);
  });

  it("fits bounds around every component, not only the first", () => {
    const bounds = boundaryBounds(boundary(MULTIPOLYGON_WITH_HOLE));

    expect(bounds.isValid()).toBe(true);
    expect(bounds.getWest()).toBeCloseTo(-122.03, 5);
    expect(bounds.getSouth()).toBeCloseTo(37.32, 5);
    expect(bounds.getEast()).toBeCloseTo(-121.93, 5);
    expect(bounds.getNorth()).toBeCloseTo(37.38, 5);
  });

  it("changes the layer key when the boundary or its mode changes", () => {
    const routed = boundaryLayerKey(boundary(MULTIPOLYGON_WITH_HOLE), CENTER);
    const polygon = boundaryLayerKey(boundary(POLYGON_WITH_HOLE), CENTER);
    const nominal = boundaryLayerKey(
      boundary(POLYGON_WITH_HOLE, "nominal_radius_circle"),
      CENTER,
    );
    const later = boundaryLayerKey(
      boundary(POLYGON_WITH_HOLE, "routed_isochrone", "2026-09-02T00:00:00Z"),
      CENTER,
    );

    expect(new Set([routed, polygon, nominal, later]).size).toBe(4);
    expect(boundaryLayerKey(boundary(POLYGON_WITH_HOLE), CENTER)).toBe(polygon);
  });
});

describe("AreaMap rendering", () => {
  // jsdom reports a 0×0 layout; Leaflet clips every path to the container,
  // so give it a viewport large enough to hold the whole fixture at zoom 13.
  const originalWidth = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "clientWidth",
  );
  const originalHeight = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "clientHeight",
  );

  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get: () => 2_000,
    });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      get: () => 2_000,
    });
  });

  afterEach(() => {
    if (originalWidth) {
      Object.defineProperty(HTMLElement.prototype, "clientWidth", originalWidth);
    }
    if (originalHeight) {
      Object.defineProperty(HTMLElement.prototype, "clientHeight", originalHeight);
    }
  });

  it("draws a MultiPolygon with a hole as one path with three rings", () => {
    const { container } = render(
      <AreaMap
        boundary={boundary(MULTIPOLYGON_WITH_HOLE)}
        facilities={facilities(0)}
        center={CENTER}
        centerName="Test center"
      />,
    );

    const path = container.querySelector<SVGPathElement>(
      ".leaflet-overlay-pane path",
    );
    expect(path).not.toBeNull();
    const d = path?.getAttribute("d") ?? "";
    expect(d.match(/M/g)).toHaveLength(3);
    expect(d.match(/z/g)).toHaveLength(3);
  });

  it("draws a Polygon with a hole as one path with two rings", () => {
    const { container } = render(
      <AreaMap
        boundary={boundary(POLYGON_WITH_HOLE)}
        facilities={facilities(0)}
        center={CENTER}
        centerName="Test center"
      />,
    );

    const d =
      container
        .querySelector<SVGPathElement>(".leaflet-overlay-pane path")
        ?.getAttribute("d") ?? "";
    expect(d.match(/M/g)).toHaveLength(2);
  });
});
