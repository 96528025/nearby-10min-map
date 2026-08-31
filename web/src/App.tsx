import { useCallback, useEffect, useReducer, useRef, useState } from "react";
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
import {
  MAX_CONSECUTIVE_POLL_FAILURES,
  POLL_MAX_DURATION_MS,
  pollDelayMs,
} from "./state/polling";
import {
  initialWorkflowState,
  workflowReducer,
  type WorkflowState,
} from "./state/workflow";

const APPLE_PARK = { lat: 37.33484, lon: -122.01139 };

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

interface RequestContext {
  controller: AbortController;
  generation: number;
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

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === "AbortError";
}

function statusLabel(status?: AreaStatus) {
  if (status === "enriching") return "Enriching";
  if (status === "complete") return "Complete";
  if (status === "osm_only") return "OSM-only";
  return "Bundled snapshot";
}

function noticeForWorkflow(
  workflow: WorkflowState,
  defaultLoading: boolean,
): Notice {
  switch (workflow.status) {
    case "idle":
      return defaultLoading
        ? {
            tone: "loading",
            message: "Loading the bundled Apple Park snapshot…",
          }
        : {
            tone: "info",
            message:
              "Bundled Apple Park data loaded; no geocoding, routing, or POI API lookup was made.",
          };
    case "geocoding":
      return {
        tone: "loading",
        message: `Searching for “${workflow.query}”…`,
      };
    case "candidates":
      return {
        tone: "info",
        message: "Choose the intended place before computing its area.",
      };
    case "empty":
      return {
        tone: "warning",
        message: "没有找到该地点 · No matching places found.",
      };
    case "loadingArea":
      return {
        tone: "loading",
        message: `Computing the approximate 10-minute drive area for ${workflow.selectedCandidate?.name ?? "the selected place"}…`,
      };
    case "enriching":
      if (workflow.pollFailureCount > 0) {
        return {
          tone: "warning",
          message: `The OSM map remains available. An enrichment update failed (${workflow.pollFailureCount}/${MAX_CONSECUTIVE_POLL_FAILURES}); retrying automatically…`,
        };
      }
      return {
        tone: "loading",
        message:
          "OSM facilities are visible now. Overture enrichment is still running…",
      };
    case "complete":
      return {
        tone: "success",
        message: `设施补全完成 · Facilities complete (${workflow.latestArea?.total ?? 0})`,
      };
    case "osmOnly":
      return {
        tone: "warning",
        message:
          "当前为 OSM-only 结果 · Overture enrichment failed; the map data remains usable.",
      };
    case "error": {
      const prefix = {
        default: "Default data failed to load",
        geocode: "Search failed",
        area: "Area computation failed",
        poll: "Area enrichment stopped",
      }[workflow.errorStage];
      return {
        tone: "error",
        message: `${prefix}: ${workflow.errorMessage}`,
      };
    }
  }
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
  const [workflow, dispatch] = useReducer(
    workflowReducer,
    initialWorkflowState,
  );
  const [defaultArea, setDefaultArea] = useState<DisplayArea | null>(null);
  const [defaultLoading, setDefaultLoading] = useState(true);
  const [query, setQuery] = useState("");
  const pollTimer = useRef<number | null>(null);
  const pollDeadlineTimer = useRef<number | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const mounted = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
    if (pollDeadlineTimer.current !== null) {
      window.clearTimeout(pollDeadlineTimer.current);
      pollDeadlineTimer.current = null;
    }
  }, []);

  const beginRequest = useCallback((): RequestContext => {
    stopPolling();
    generation.current += 1;
    activeController.current?.abort();

    const controller = new AbortController();
    activeController.current = controller;
    return { controller, generation: generation.current };
  }, [stopPolling]);

  const isCurrentRequest = useCallback(
    (requestGeneration: number) =>
      mounted.current && generation.current === requestGeneration,
    [],
  );

  const releaseRequest = useCallback((controller: AbortController) => {
    if (activeController.current === controller) {
      activeController.current = null;
    }
  }, []);

  const loadBundledDefault = useCallback(async () => {
    const request = beginRequest();
    setDefaultLoading(true);

    try {
      const { boundary, facilities, landmarks } = await loadDefaultData(
        request.controller.signal,
      );
      if (!isCurrentRequest(request.generation)) return;

      setDefaultArea({
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
      dispatch({ type: "DEFAULT_READY" });
    } catch (error: unknown) {
      if (isAbortError(error) || !isCurrentRequest(request.generation)) return;
      dispatch({ type: "DEFAULT_FAILURE", error: errorMessage(error) });
    } finally {
      if (isCurrentRequest(request.generation)) {
        setDefaultLoading(false);
        releaseRequest(request.controller);
      }
    }
  }, [beginRequest, isCurrentRequest, releaseRequest]);

  useEffect(() => {
    mounted.current = true;
    void loadBundledDefault();

    return () => {
      mounted.current = false;
      generation.current += 1;
      activeController.current?.abort();
      activeController.current = null;
      stopPolling();
    };
  }, [loadBundledDefault, stopPolling]);

  const dynamicArea =
    workflow.latestArea && workflow.selectedCandidate
      ? displayAreaFromResponse(
          workflow.latestArea,
          workflow.selectedCandidate,
        )
      : null;
  const displayArea = dynamicArea ?? defaultArea;

  const runSearch = useCallback(
    async (submittedQuery: string) => {
      const request = beginRequest();
      setDefaultLoading(false);
      dispatch({ type: "GEOCODE_START", query: submittedQuery });

      try {
        const bias = displayArea?.center ?? APPLE_PARK;
        const response = await geocode(
          {
            query: submittedQuery,
            biasLat: Number(bias.lat.toFixed(4)),
            biasLon: Number(bias.lon.toFixed(4)),
          },
          request.controller.signal,
        );
        if (!isCurrentRequest(request.generation)) return;

        dispatch({
          type: "GEOCODE_SUCCESS",
          query: submittedQuery,
          candidates: response.candidates,
        });
      } catch (error: unknown) {
        if (isAbortError(error) || !isCurrentRequest(request.generation)) return;
        dispatch({
          type: "GEOCODE_FAILURE",
          query: submittedQuery,
          error: errorMessage(error),
        });
      } finally {
        if (isCurrentRequest(request.generation)) {
          releaseRequest(request.controller);
        }
      }
    },
    [beginRequest, displayArea?.center, isCurrentRequest, releaseRequest],
  );

  const runArea = useCallback(
    async (candidate: GeocodeCandidate) => {
      const request = beginRequest();
      setDefaultLoading(false);
      dispatch({ type: "AREA_START", candidate });

      const requestArea = () =>
        fetchArea(
          {
            lat: candidate.lat,
            lon: candidate.lon,
            name: candidate.name,
          },
          request.controller.signal,
        );

      const finishPollingWithError = (message: string) => {
        if (!isCurrentRequest(request.generation)) return;
        stopPolling();
        releaseRequest(request.controller);
        dispatch({
          type: "POLL_TIMEOUT",
          candidate,
          error: message,
        });
      };

      const pollStartedAt = Date.now();
      const timeoutMessage =
        "Polling reached its two-minute limit. The current map remains available; retry to continue.";
      const pollingIsActive = () =>
        isCurrentRequest(request.generation) &&
        !request.controller.signal.aborted;

      const schedulePollingDeadline = () => {
        const remaining =
          POLL_MAX_DURATION_MS - (Date.now() - pollStartedAt);
        if (remaining <= 0) {
          finishPollingWithError(timeoutMessage);
          return;
        }

        pollDeadlineTimer.current = window.setTimeout(() => {
          if (!isCurrentRequest(request.generation)) return;
          request.controller.abort();
          finishPollingWithError(timeoutMessage);
        }, remaining);
      };

      const schedulePoll = (
        attempt: number,
        consecutiveFailures: number,
      ) => {
        if (!pollingIsActive()) return;

        const remaining =
          POLL_MAX_DURATION_MS - (Date.now() - pollStartedAt);
        if (remaining <= 0) {
          finishPollingWithError(timeoutMessage);
          return;
        }

        const delay = Math.min(pollDelayMs(attempt), remaining);
        pollTimer.current = window.setTimeout(() => {
          pollTimer.current = null;
          void poll(attempt, consecutiveFailures);
        }, delay);
      };

      const poll = async (attempt: number, consecutiveFailures: number) => {
        if (!pollingIsActive()) return;

        if (Date.now() - pollStartedAt >= POLL_MAX_DURATION_MS) {
          finishPollingWithError(timeoutMessage);
          return;
        }

        try {
          const response = await requestArea();
          if (!pollingIsActive()) return;

          dispatch({ type: "AREA_RESPONSE", candidate, response });
          if (response.status === "enriching") {
            schedulePoll(attempt + 1, 0);
          } else {
            stopPolling();
            releaseRequest(request.controller);
          }
        } catch (error: unknown) {
          if (isAbortError(error) || !isCurrentRequest(request.generation)) {
            return;
          }

          const nextFailureCount = consecutiveFailures + 1;
          if (nextFailureCount >= MAX_CONSECUTIVE_POLL_FAILURES) {
            finishPollingWithError(
              `The backend could not be reached after ${MAX_CONSECUTIVE_POLL_FAILURES} attempts (${errorMessage(error)}). The current map remains available; retry when the service is ready.`,
            );
            return;
          }

          dispatch({
            type: "POLL_TRANSIENT_RETRY",
            candidate,
            error: errorMessage(error),
          });
          schedulePoll(attempt + 1, nextFailureCount);
        }
      };

      try {
        const response = await requestArea();
        if (!isCurrentRequest(request.generation)) return;

        dispatch({ type: "AREA_RESPONSE", candidate, response });
        if (response.status === "enriching") {
          schedulePollingDeadline();
          schedulePoll(0, 0);
        } else {
          stopPolling();
          releaseRequest(request.controller);
        }
      } catch (error: unknown) {
        if (isAbortError(error) || !isCurrentRequest(request.generation)) return;
        releaseRequest(request.controller);
        dispatch({
          type: "AREA_FAILURE",
          candidate,
          error: errorMessage(error),
        });
      }
    },
    [beginRequest, isCurrentRequest, releaseRequest, stopPolling],
  );

  const handleSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submittedQuery = query.trim();
    if (!submittedQuery) return;
    void runSearch(submittedQuery);
  };

  const handleRetry = () => {
    if (workflow.status !== "error") return;

    if (workflow.errorStage === "default") {
      void loadBundledDefault();
      return;
    }
    if (workflow.errorStage === "geocode") {
      void runSearch(workflow.query);
      return;
    }
    if (workflow.selectedCandidate) {
      void runArea(workflow.selectedCandidate);
    }
  };

  const notice = noticeForWorkflow(workflow, defaultLoading);
  const radiusKm = displayArea
    ? (displayArea.boundary.metadata.radius_m / 1_000).toFixed(1)
    : "—";
  const busy =
    workflow.status === "geocoding" || workflow.status === "loadingArea";

  return (
    <main className="app-shell" data-workflow-state={workflow.status}>
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
              disabled={query.trim().length === 0}
              aria-busy={busy}
            >
              {workflow.status === "geocoding" ? "Searching…" : "Search"}
            </button>
          </form>

          {workflow.candidates.length > 0 ? (
            <ul className="candidate-list" aria-label="Geocoding candidates">
              {workflow.candidates.map((candidate) => (
                <li key={`${candidate.osm}:${candidate.lat}:${candidate.lon}`}>
                  <button
                    className="candidate-button"
                    type="button"
                    onClick={() => void runArea(candidate)}
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
        <span className="status-card__message">{notice.message}</span>
        {workflow.status === "error" ? (
          <button className="retry-button" type="button" onClick={handleRetry}>
            Retry
          </button>
        ) : null}
      </div>

      {displayArea ? (
        <>
          <section className="area-summary" aria-label="Displayed area summary">
            <div className="area-summary__identity">
              <span
                className={`state-badge state-badge--${displayArea.status ?? "default"}`}
              >
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
                {displayArea.boundary.metadata.generated_utc} · Facilities{" "}
                {displayArea.facilities.metadata.generated_utc}
              </p>
            </div>
          </section>
        </>
      ) : (
        <section className="empty-map" aria-label="Map loading placeholder">
          {workflow.status !== "error" ? (
            <span className="spinner" aria-hidden="true" />
          ) : null}
          <p>
            {workflow.status === "error"
              ? "No map data is currently available."
              : "Preparing the bundled map…"}
          </p>
        </section>
      )}

      <footer className="app-footer">
        Approximate 10-minute drive, free-flow conditions; no real-time traffic.
        Facility coverage may be incomplete.
      </footer>
    </main>
  );
}
