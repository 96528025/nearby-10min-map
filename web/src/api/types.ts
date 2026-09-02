export type AreaStatus = "enriching" | "complete" | "osm_only";

/**
 * Provenance of the displayed boundary. `routed_isochrone` is the Valhalla
 * 10-minute isochrone rendered as returned; `nominal_radius_circle` is the
 * explicit fixed-radius fallback used when routing is unavailable. The
 * retired `routed_equal_area_circle` is no longer produced by the API.
 */
export type BoundaryMode = "routed_isochrone" | "nominal_radius_circle";

export interface GeocodeCandidate {
  name: string;
  display_name: string;
  lat: number;
  lon: number;
  osm: string;
}

export interface GeocodeResponse {
  candidates: GeocodeCandidate[];
}

export type Position = [longitude: number, latitude: number];

/** Outer ring first, then any interior rings (holes). */
export interface PolygonGeometry {
  type: "Polygon";
  coordinates: Position[][];
}

/** One ring list per component; each component may carry holes. */
export interface MultiPolygonGeometry {
  type: "MultiPolygon";
  coordinates: Position[][][];
}

export type BoundaryGeometry = PolygonGeometry | MultiPolygonGeometry;

export interface BoundaryFeature {
  type: "Feature";
  properties: {
    contour: string;
    [key: string]: unknown;
  };
  geometry: BoundaryGeometry;
}

export interface BoundaryMetadata {
  generated_utc: string;
  method: string;
  center: {
    lat: number;
    lon: number;
    name: string;
  };
  /** Only the fixed nominal-radius fallback has a radius. */
  radius_m?: number;
  /** Planar area of the routed isochrone: every component, holes subtracted. */
  isochrone_area_km2?: number;
  geometry_type?: BoundaryGeometry["type"];
  geometry_components?: number;
  geometry_holes?: number;
  contour_minutes?: number;
  /** Valhalla contour denoising threshold used before geometry is returned. */
  denoise?: number;
  traffic?: string;
  source?: string;
  requested_point?: {
    lat: number;
    lon: number;
  };
  snap_distance_m?: number | null;
  [key: string]: unknown;
}

export interface BoundaryFeatureCollection {
  type: "FeatureCollection";
  /**
   * Declared by the boundary artifact itself. The bundled snapshot relies on
   * this field; the client never infers provenance from the geometry's shape.
   */
  boundary_mode?: BoundaryMode;
  metadata: BoundaryMetadata;
  features: BoundaryFeature[];
}

export interface Facility {
  name: string;
  lat: number;
  lon: number;
  kind: string | null;
  addr: string | null;
  osm?: string;
  src?: string;
}

export interface FacilityCategory {
  label_zh: string;
  label_en: string;
  color: string;
  count: number;
  items: Facility[];
}

export type FacilityCategoryKey =
  | "dining"
  | "health"
  | "education"
  | "lodging"
  | "shopping"
  | "fuel_ev"
  | "culture"
  | "parks";

export interface FacilitiesMetadata {
  generated_utc: string;
  source: string;
  filter: string;
  overture_min_confidence?: number;
  overture_release?: string;
  overture_attribution?: string;
  overture_modifications?: string;
  [key: string]: unknown;
}

export interface FacilitiesCollection {
  metadata: FacilitiesMetadata;
  categories: Record<FacilityCategoryKey, FacilityCategory>;
}

export interface Landmark {
  name_en: string;
  name_zh: string;
  lat: number;
  lon: number;
  drive_min: number;
  drive_km: number;
  desc_en: string;
  desc_zh: string;
  osm: string;
}

export interface AreaResponse {
  status: AreaStatus;
  name: string;
  lat: number;
  lon: number;
  boundary: BoundaryFeatureCollection;
  facilities: FacilitiesCollection;
  total: number;
  enrich_error?: boolean;
  boundary_mode?: BoundaryMode;
  warnings?: string[];
}

export interface DefaultViewData {
  boundary: BoundaryFeatureCollection;
  facilities: FacilitiesCollection;
  landmarks: Landmark[];
}
