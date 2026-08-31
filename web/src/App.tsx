import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import {
  area as fetchArea,
  geocode,
  loadDefaultData,
} from "./api/client";
import type {
  AreaResponse,
  AreaStatus,
  BoundaryFeatureCollection,
  BoundaryMode,
  FacilitiesCollection,
  GeocodeCandidate,
  Landmark,
} from "./api/types";
import { AreaMap } from "./components/AreaMap";

const APPLE_PARK = { lat: 37.33484, lon: -122.01139 };
const POLL_INTERVAL_MS = 6_000;

type NoticeTone = "info" | "loading" | "success" | "warning" | "error";

interface Notice {
  tone: NoticeTone;
  message: string;
}

interface DisplayArea {
  name: string;
  locationLabel: string;
  center: { lat: number; lon: number };
  boundary: BoundaryFeatureCollection;
  facilities: FacilitiesCollection;
  landmarks: Landmark[];
  total: number;
  status?: AreaStatus;
  boundaryMode?: BoundaryMode;
  warnings: string[];
}

function facilityTotal(facilities: FacilitiesCollection) {
  return Object.values(facilities.categories).reduce(
    (total, category) => total + category.count,
    0,
  );
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Unknown error";
}

function statusLabel(status?: AreaStatus) {
  if (status === "enriching") return "Enriching";
  if (status === "complete") return "Complete";
  if (status === "osm_only") return "OSM-only";
  return "Bundled snapshot";
}

function noticeFor(area: AreaResponse): Notice {
  if (area.status === "enriching") {
    return {
      tone: "loading",
      message:
        "OSM facilities are visible now. Overture enrichment is still running…",
    };
  }

  if (area.status === "osm_only") {
    return {
      tone: "warning",
      message:
        "当前为 OSM-only 结果 · Overture enrichment failed; the map data remains usable.",
    };
  }

  return {
    tone: "success",
    message: `设施补全完成 · Facilities complete (${area.total})`,
  };
}

function displayAreaFromResponse(
  response: AreaResponse,
  candidate: GeocodeCandidate,
): DisplayArea {
  return {
    name: candidate.name,
    locationLabel: candidate.display_name,
    center: { lat: response.lat, lon: response.lon },
    boundary: response.boundary,
    facilities: response.facilities,
    landmarks: [],
    total: response.total,
    status: response.status,
    boundaryMode: response.boundary_mode,
    warnings: response.warnings ?? [],
  };
}

export default function App() {
  const [displayArea, setDisplayArea] = useState<DisplayArea | null>(null);
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<GeocodeCandidate[]>([]);
  const [geocoding, setGeocoding] = useState(false);
  const [loadingArea, setLoadingArea] = useState(false);
  const [notice, setNotice] = useState<Notice>({
    tone: "loading",
    message: "Loading the bundled Apple Park snapshot…",
  });
  const pollTimer = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    loadDefaultData(controller.signal)
      .then(({ boundary, facilities, landmarks }) => {
        setDisplayArea({
          name: "Apple Park",
          locationLabel:
            "Bundled static data · Apple Park, Cupertino, California",
          center: APPLE_PARK,
          boundary,
          facilities,
          landmarks,
          total: facilityTotal(facilities),
          warnings: [],
        });
        setNotice({
          tone: "info",
          message:
            "Bundled Apple Park data loaded; no geocoding, routing, or POI API lookup was made.",
        });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setNotice({
          tone: "error",
          message: `Default data failed to load: ${errorMessage(error)}`,
        });
      });

    return () => {
      controller.abort();
      stopPolling();
    };
  }, [stopPolling]);

  const handleSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submittedQuery = query.trim();
    if (!submittedQuery) return;

    setCandidates([]);
    setGeocoding(true);
    setNotice({
      tone: "loading",
      message: `Searching for “${submittedQuery}”…`,
    });

    try {
      const bias = displayArea?.center ?? APPLE_PARK;
      const response = await geocode({
        query: submittedQuery,
        biasLat: Number(bias.lat.toFixed(4)),
        biasLon: Number(bias.lon.toFixed(4)),
      });

      setCandidates(response.candidates);
      setNotice(
        response.candidates.length > 0
          ? {
              tone: "info",
              message: "Choose the intended place before computing its area.",
            }
          : {
              tone: "warning",
              message: "没有找到该地点 · No matching places found.",
            },
      );
    } catch (error: unknown) {
      setNotice({
        tone: "error",
        message: `Search failed: ${errorMessage(error)}`,
      });
    } finally {
      setGeocoding(false);
    }
  };

  const handleCandidate = async (candidate: GeocodeCandidate) => {
    stopPolling();
    setCandidates([]);
    setLoadingArea(true);
    setNotice({
      tone: "loading",
      message: `Computing the approximate 10-minute drive area for ${candidate.name}…`,
    });

    const requestArea = () =>
      fetchArea({
        lat: candidate.lat,
        lon: candidate.lon,
        name: candidate.name,
      });

    const applyResponse = (response: AreaResponse) => {
      setDisplayArea(displayAreaFromResponse(response, candidate));
      setNotice(noticeFor(response));
    };

    const schedulePoll = () => {
      pollTimer.current = window.setTimeout(async () => {
        try {
          const response = await requestArea();
          applyResponse(response);
          if (response.status === "enriching") schedulePoll();
          else pollTimer.current = null;
        } catch {
          schedulePoll();
        }
      }, POLL_INTERVAL_MS);
    };

    try {
      const response = await requestArea();
      applyResponse(response);
      if (response.status === "enriching") schedulePoll();
    } catch (error: unknown) {
      setNotice({
        tone: "error",
        message: `Area computation failed: ${errorMessage(error)}`,
      });
    } finally {
      setLoadingArea(false);
    }
  };

  const radiusKm = displayArea
    ? (displayArea.boundary.metadata.radius_m / 1_000).toFixed(1)
    : "—";

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">DRIVE-TIME CONTEXT · 驾车范围</p>
          <h1 className="app-title">What is within roughly 10 minutes?</h1>
          <p className="app-subtitle">
            Submit a destination, confirm the intended place, and explore a
            driving area with nearby visitor facilities.
          </p>
        </div>

        <div className="search-panel">
          <form className="search-form" onSubmit={handleSearch}>
            <label className="sr-only" htmlFor="place-search">
              Destination or attraction
            </label>
            <input
              id="place-search"
              className="search-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Apple Park, Stanford University, SJC Airport…"
              autoComplete="off"
            />
            <button
              className="primary-button"
              type="submit"
              disabled={geocoding || loadingArea || query.trim().length === 0}
            >
              {geocoding ? "Searching…" : "Search"}
            </button>
          </form>

          {candidates.length > 0 ? (
            <ul className="candidate-list" aria-label="Geocoding candidates">
              {candidates.map((candidate) => (
                <li key={`${candidate.osm}:${candidate.lat}:${candidate.lon}`}>
                  <button
                    className="candidate-button"
                    type="button"
                    onClick={() => void handleCandidate(candidate)}
                    disabled={loadingArea}
                  >
                    <strong>{candidate.name}</strong>
                    <small>{candidate.display_name}</small>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </header>

      <div
        className={`status-card status-card--${notice.tone}`}
        role={notice.tone === "error" ? "alert" : "status"}
        aria-live="polite"
      >
        {notice.tone === "loading" ? (
          <span className="spinner" aria-hidden="true" />
        ) : null}
        <span>{notice.message}</span>
      </div>

      {displayArea ? (
        <>
          <section className="area-summary" aria-label="Displayed area summary">
            <div className="area-summary__identity">
              <span className={`state-badge state-badge--${displayArea.status ?? "default"}`}>
                {statusLabel(displayArea.status)}
              </span>
              <h2>{displayArea.name}</h2>
              <p>{displayArea.locationLabel}</p>
            </div>
            <dl className="area-stats">
              <div>
                <dt>Boundary radius</dt>
                <dd>{radiusKm} km</dd>
              </div>
              <div>
                <dt>Facilities</dt>
                <dd>{displayArea.total.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Calculation</dt>
                <dd>Free-flow driving</dd>
              </div>
            </dl>
          </section>

          {displayArea.warnings.map((warning) => (
            <div className="status-card status-card--warning" key={warning}>
              {warning}
            </div>
          ))}

          <div className="map-card">
            <AreaMap
              boundary={displayArea.boundary}
              facilities={displayArea.facilities}
              center={displayArea.center}
              centerName={displayArea.name}
              landmarks={displayArea.landmarks}
            />
          </div>

          <section className="provenance-card" aria-label="Data provenance">
            <div>
              <span>Boundary</span>
              <p>{displayArea.boundary.metadata.method}</p>
            </div>
            <div>
              <span>Facility filter</span>
              <p>{displayArea.facilities.metadata.filter}</p>
            </div>
            <div>
              <span>Generated</span>
              <p>
                {displayArea.boundary.metadata.generated_utc} · Facilities {" "}
                {displayArea.facilities.metadata.generated_utc}
              </p>
            </div>
          </section>
        </>
      ) : (
        <section className="empty-map" aria-label="Map loading placeholder">
          <span className="spinner" aria-hidden="true" />
          <p>Preparing the bundled map…</p>
        </section>
      )}

      <footer className="app-footer">
        Approximate 10-minute drive, free-flow conditions; no real-time traffic.
        Facility coverage may be incomplete.
      </footer>
    </main>
  );
}
