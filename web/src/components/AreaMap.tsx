import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import {
  CircleMarker,
  GeoJSON,
  MapContainer,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";

import "leaflet/dist/leaflet.css";
import "../styles.css";

import type {
  BoundaryFeatureCollection,
  FacilitiesCollection,
  FacilityCategory,
  FacilityCategoryKey,
  Landmark,
} from "../api/types";

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const BOUNDARY_STYLE: L.PathOptions = {
  color: "#2563eb",
  fillColor: "#3b82f6",
  fillOpacity: 0.16,
  opacity: 0.92,
  weight: 2.5,
};

export interface AreaMapCenter {
  lat: number;
  lon: number;
}

export interface AreaMapProps {
  boundary: BoundaryFeatureCollection;
  facilities: FacilitiesCollection;
  center: AreaMapCenter;
  centerName: string;
  landmarks?: readonly Landmark[];
  className?: string;
}

interface FitBoundaryProps {
  boundary: BoundaryFeatureCollection;
}

function FitBoundary({ boundary }: FitBoundaryProps) {
  const map = useMap();

  useEffect(() => {
    const bounds = L.geoJSON(boundary).getBounds();
    if (!bounds.isValid()) return;

    const frame = window.requestAnimationFrame(() => {
      map.invalidateSize({ pan: false });
      map.fitBounds(bounds, {
        animate: false,
        maxZoom: 15,
        padding: [36, 36],
      });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [boundary, map]);

  return null;
}

function formatKind(kind: string | null) {
  if (!kind) return null;
  return kind.replaceAll("_", " ");
}

function mapsUrl(name: string, lat: number, lon: number, address?: string | null) {
  const query = address ? `${name} ${address}` : `${name} ${lat},${lon}`;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
}

function FacilityPopup({ category, item }: {
  category: FacilityCategory;
  item: FacilityCategory["items"][number];
}) {
  const kind = formatKind(item.kind);

  return (
    <div className="map-popup">
      <strong className="map-popup__title">{item.name}</strong>
      <span className="map-popup__eyebrow">
        {category.label_zh} · {category.label_en}
        {kind ? ` · ${kind}` : ""}
      </span>
      {item.addr ? <span className="map-popup__address">{item.addr}</span> : null}
      <span className="map-popup__note">
        位于约 10 分钟驾车范围内 · Within the ~10-minute driving range
      </span>
      <a
        className="map-popup__link"
        href={mapsUrl(item.name, item.lat, item.lon, item.addr)}
        target="_blank"
        rel="noreferrer"
      >
        Open in Google Maps ↗
      </a>
    </div>
  );
}

function LandmarkPopup({ landmark }: { landmark: Landmark }) {
  return (
    <div className="map-popup">
      <strong className="map-popup__title">{landmark.name_zh}</strong>
      <span className="map-popup__eyebrow">{landmark.name_en}</span>
      <span>{landmark.desc_zh}</span>
      <span>{landmark.desc_en}</span>
      <span className="map-popup__drive">
        🚗 约 {landmark.drive_min} 分钟 · {landmark.drive_km} km from Apple Park
      </span>
      <a
        className="map-popup__link"
        href={mapsUrl(landmark.name_en, landmark.lat, landmark.lon)}
        target="_blank"
        rel="noreferrer"
      >
        Open in Google Maps ↗
      </a>
    </div>
  );
}

export function AreaMap({
  boundary,
  facilities,
  center,
  centerName,
  landmarks = [],
  className,
}: AreaMapProps) {
  // Dining alone accounts for most of the bundled points. Keep it available but
  // initially hidden so individual markers remain useful without clustering.
  const [hiddenCategories, setHiddenCategories] = useState<Set<FacilityCategoryKey>>(
    () => new Set(["dining"]),
  );

  const categories = useMemo(
    () =>
      (Object.entries(facilities.categories) as [
        FacilityCategoryKey,
        FacilityCategory,
      ][]).filter(([, category]) => category.count > 0),
    [facilities.categories],
  );

  const boundaryKey = [
    center.lat,
    center.lon,
    boundary.metadata.radius_m,
    boundary.metadata.generated_utc,
  ].join(":");

  const toggleCategory = (key: FacilityCategoryKey) => {
    setHiddenCategories((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <section
      className={["area-map", className].filter(Boolean).join(" ")}
      aria-label={`Map of the approximately 10-minute driving range from ${centerName}`}
    >
      <MapContainer
        center={[center.lat, center.lon]}
        zoom={13}
        minZoom={3}
        maxZoom={19}
        scrollWheelZoom
        className="area-map__canvas"
      >
        <TileLayer
          attribution={TILE_ATTRIBUTION}
          maxZoom={19}
          url={TILE_URL}
        />

        <GeoJSON key={boundaryKey} data={boundary} style={BOUNDARY_STYLE} />
        <FitBoundary boundary={boundary} />

        {categories.map(([key, category]) =>
          hiddenCategories.has(key)
            ? null
            : category.items.map((item, index) => (
                <CircleMarker
                  key={`${key}:${item.osm ?? item.src ?? "place"}:${item.name}:${item.lat}:${item.lon}:${index}`}
                  center={[item.lat, item.lon]}
                  radius={5}
                  pathOptions={{
                    color: "#ffffff",
                    fillColor: category.color,
                    fillOpacity: 0.92,
                    opacity: 0.9,
                    weight: 1.5,
                  }}
                >
                  <Popup>
                    <FacilityPopup category={category} item={item} />
                  </Popup>
                </CircleMarker>
              )),
        )}

        {landmarks.map((landmark) => (
          <CircleMarker
            key={landmark.osm}
            center={[landmark.lat, landmark.lon]}
            radius={7}
            pathOptions={{
              color: "#ffffff",
              fillColor: "#f97316",
              fillOpacity: 1,
              weight: 2.5,
            }}
          >
            <Tooltip direction="top" offset={[0, -6]}>
              {landmark.name_en}
            </Tooltip>
            <Popup>
              <LandmarkPopup landmark={landmark} />
            </Popup>
          </CircleMarker>
        ))}

        <CircleMarker
          center={[center.lat, center.lon]}
          radius={9}
          pathOptions={{
            color: "#ffffff",
            fillColor: "#111827",
            fillOpacity: 1,
            weight: 3,
          }}
        >
          <Tooltip direction="top" offset={[0, -8]}>{centerName}</Tooltip>
          <Popup>
            <div className="map-popup">
              <strong className="map-popup__title">{centerName}</strong>
              <span className="map-popup__eyebrow">范围中心 · Area center</span>
              <a
                className="map-popup__link"
                href={mapsUrl(centerName, center.lat, center.lon)}
                target="_blank"
                rel="noreferrer"
              >
                Open in Google Maps ↗
              </a>
            </div>
          </Popup>
        </CircleMarker>
      </MapContainer>

      <aside className="map-legend" aria-label="Map layers">
        <div className="map-legend__heading">
          <span>设施图层</span>
          <span>Facility layers</span>
        </div>
        <div className="map-legend__items">
          {categories.map(([key, category]) => {
            const visible = !hiddenCategories.has(key);
            return (
              <label className="map-layer" key={key}>
                <input
                  type="checkbox"
                  checked={visible}
                  onChange={() => toggleCategory(key)}
                />
                <span
                  className="map-layer__dot"
                  style={{ backgroundColor: category.color }}
                  aria-hidden="true"
                />
                <span className="map-layer__label">
                  {category.label_zh} · {category.label_en}
                </span>
                <span className="map-layer__count">{category.count}</span>
              </label>
            );
          })}
        </div>
        <div className="map-legend__key">
          <span><i className="map-key map-key--area" />约 10 分钟驾车范围</span>
          <span><i className="map-key map-key--center" />中心</span>
          {landmarks.length > 0 ? (
            <span><i className="map-key map-key--landmark" />默认视图地标</span>
          ) : null}
        </div>
      </aside>
    </section>
  );
}
