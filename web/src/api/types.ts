export type AreaStatus = "enriching" | "complete" | "osm_only";

export type BoundaryMode =
  | "routed_equal_area_circle"
  | "nominal_radius_circle";

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

export interface PolygonGeometry {
  type: "Polygon";
  coordinates: Position[][];
}

export interface BoundaryFeature {
  type: "Feature";
  properties: {
    contour: string;
    [key: string]: unknown;
  };
  geometry: PolygonGeometry;
}

export interface BoundaryMetadata {
  generated_utc: string;
  method: string;
  center: {
    lat: number;
    lon: number;
    name: string;
  };
  radius_m: number;
  isochrone_area_km2?: number;
  calibration?: string;
  requested_point?: {
    lat: number;
    lon: number;
  };
  snap_distance_m?: number | null;
  [key: string]: unknown;
}

export interface BoundaryFeatureCollection {
  type: "FeatureCollection";
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
