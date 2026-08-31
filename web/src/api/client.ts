import type {
  AreaResponse,
  BoundaryFeatureCollection,
  DefaultViewData,
  FacilitiesCollection,
  GeocodeResponse,
  Landmark,
} from "./types";

export interface GeocodeParams {
  query: string;
  biasLat?: number;
  biasLon?: number;
}

export interface AreaParams {
  lat: number;
  lon: number;
  name?: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, signal ? { signal } : undefined);

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      isRecord(body) && typeof body.detail === "string"
        ? body.detail
        : `${response.status} ${response.statusText}`.trim();
    throw new ApiError(detail || "Request failed", response.status);
  }

  return (await response.json()) as T;
}

export function geocode(
  { query, biasLat, biasLon }: GeocodeParams,
  signal?: AbortSignal,
): Promise<GeocodeResponse> {
  const params = new URLSearchParams({ q: query });
  if (biasLat !== undefined) params.set("bias_lat", String(biasLat));
  if (biasLon !== undefined) params.set("bias_lon", String(biasLon));

  return requestJson<GeocodeResponse>(`/api/geocode?${params}`, signal);
}

export function area(
  { lat, lon, name }: AreaParams,
  signal?: AbortSignal,
): Promise<AreaResponse> {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
  });
  if (name !== undefined) params.set("name", name);

  return requestJson<AreaResponse>(`/api/area?${params}`, signal);
}

export function loadDefaultBoundary(
  signal?: AbortSignal,
): Promise<BoundaryFeatureCollection> {
  return requestJson<BoundaryFeatureCollection>("/data/boundary.json", signal);
}

export function loadDefaultFacilities(
  signal?: AbortSignal,
): Promise<FacilitiesCollection> {
  return requestJson<FacilitiesCollection>("/data/facilities.json", signal);
}

export function loadDefaultLandmarks(
  signal?: AbortSignal,
): Promise<Landmark[]> {
  return requestJson<Landmark[]>("/data/landmarks.json", signal);
}

export async function loadDefaultData(
  signal?: AbortSignal,
): Promise<DefaultViewData> {
  const [boundary, facilities, landmarks] = await Promise.all([
    loadDefaultBoundary(signal),
    loadDefaultFacilities(signal),
    loadDefaultLandmarks(signal),
  ]);

  return { boundary, facilities, landmarks };
}
