import { describe, expect, it } from "vitest";

import type {
  AreaResponse,
  AreaStatus,
  FacilityCategory,
  FacilitiesCollection,
  GeocodeCandidate,
} from "../api/types";
import {
  initialWorkflowState,
  workflowReducer,
  type WorkflowEvent,
  type WorkflowState,
} from "./workflow";

const candidate: GeocodeCandidate = {
  name: "Stanford University",
  display_name: "Stanford University, California",
  lat: 37.4275,
  lon: -122.1697,
  osm: "relation/3377986",
};

const otherCandidate: GeocodeCandidate = {
  name: "San José State University",
  display_name: "San José State University, San Jose, California",
  lat: 37.3352,
  lon: -121.8811,
  osm: "relation/1413691",
};

function category(label: string): FacilityCategory {
  return {
    label_zh: label,
    label_en: label,
    color: "#000000",
    count: 0,
    items: [],
  };
}

function facilities(): FacilitiesCollection {
  return {
    metadata: {
      generated_utc: "2026-08-30T00:00:00Z",
      source: "OpenStreetMap",
      filter: "test fixture",
    },
    categories: {
      dining: category("dining"),
      health: category("health"),
      education: category("education"),
      lodging: category("lodging"),
      shopping: category("shopping"),
      fuel_ev: category("fuel_ev"),
      culture: category("culture"),
      parks: category("parks"),
    },
  };
}

function areaResponse(status: AreaStatus): AreaResponse {
  return {
    status,
    name: candidate.name,
    lat: candidate.lat,
    lon: candidate.lon,
    boundary: {
      type: "FeatureCollection",
      metadata: {
        generated_utc: "2026-08-30T00:00:00Z",
        method: "test fixture",
        center: { lat: candidate.lat, lon: candidate.lon, name: candidate.name },
        radius_m: 5_000,
      },
      features: [],
    },
    facilities: facilities(),
    total: 0,
    enrich_error: status === "osm_only" ? true : undefined,
  };
}

function reduce(events: readonly WorkflowEvent[]): WorkflowState {
  return events.reduce(workflowReducer, initialWorkflowState);
}

function selectCandidate(): WorkflowEvent[] {
  return [
    { type: "GEOCODE_START", query: "Stanford" },
    {
      type: "GEOCODE_SUCCESS",
      query: "Stanford",
      candidates: [candidate],
    },
    { type: "AREA_START", candidate },
  ];
}

describe("workflowReducer", () => {
  it("starts idle and accepts a successful bundled default view", () => {
    const state = workflowReducer(initialWorkflowState, {
      type: "DEFAULT_READY",
    });

    expect(state).toEqual(initialWorkflowState);
  });

  it("represents a default-data error and can recover when retry succeeds", () => {
    const failed = workflowReducer(initialWorkflowState, {
      type: "DEFAULT_FAILURE",
      error: "Default data is unavailable",
    });

    expect(failed).toMatchObject({
      status: "error",
      errorStage: "default",
      errorMessage: "Default data is unavailable",
    });
    expect(workflowReducer(failed, { type: "DEFAULT_READY" })).toMatchObject({
      status: "idle",
      errorStage: null,
      errorMessage: null,
    });
  });

  it("follows the successful enriching-to-complete path", () => {
    const loadingArea = reduce(selectCandidate());
    expect(loadingArea).toMatchObject({
      status: "loadingArea",
      query: "Stanford",
      candidates: [],
      selectedCandidate: candidate,
      latestArea: null,
    });

    const enrichingResponse = areaResponse("enriching");
    const enriching = workflowReducer(loadingArea, {
      type: "AREA_RESPONSE",
      candidate,
      response: enrichingResponse,
    });
    expect(enriching).toMatchObject({
      status: "enriching",
      latestArea: enrichingResponse,
      pollFailureCount: 0,
    });

    const completeResponse = areaResponse("complete");
    const complete = workflowReducer(enriching, {
      type: "AREA_RESPONSE",
      candidate,
      response: completeResponse,
    });
    expect(complete).toMatchObject({
      status: "complete",
      latestArea: completeResponse,
      errorStage: null,
    });
  });

  it("maps an empty geocode response to the empty terminal state", () => {
    const state = reduce([
      { type: "GEOCODE_START", query: "nowhere" },
      { type: "GEOCODE_SUCCESS", query: "nowhere", candidates: [] },
    ]);

    expect(state).toMatchObject({
      status: "empty",
      query: "nowhere",
      candidates: [],
    });
  });

  it("maps a non-empty geocode response to candidates", () => {
    const state = reduce([
      { type: "GEOCODE_START", query: "Stanford" },
      {
        type: "GEOCODE_SUCCESS",
        query: "Stanford",
        candidates: [candidate, otherCandidate],
      },
    ]);

    expect(state).toMatchObject({
      status: "candidates",
      candidates: [candidate, otherCandidate],
    });
  });

  it("represents a geocoding failure and retains the query for retry", () => {
    const state = reduce([
      { type: "GEOCODE_START", query: "Stanford" },
      {
        type: "GEOCODE_FAILURE",
        query: "Stanford",
        error: "Search failed",
      },
    ]);

    expect(state).toMatchObject({
      status: "error",
      query: "Stanford",
      errorStage: "geocode",
      errorMessage: "Search failed",
    });
  });

  it("represents an initial area failure and retains the selection for retry", () => {
    const state = reduce([
      ...selectCandidate(),
      { type: "AREA_FAILURE", candidate, error: "Area failed" },
    ]);

    expect(state).toMatchObject({
      status: "error",
      selectedCandidate: candidate,
      errorStage: "area",
      errorMessage: "Area failed",
    });
  });

  it("maps osm_only to the distinct osmOnly terminal state", () => {
    const response = areaResponse("osm_only");
    const state = reduce([
      ...selectCandidate(),
      { type: "AREA_RESPONSE", candidate, response },
    ]);

    expect(state).toMatchObject({
      status: "osmOnly",
      latestArea: response,
      errorStage: null,
    });
  });

  it("keeps enriching data during transient poll failures then times out", () => {
    const response = areaResponse("enriching");
    const enriching = reduce([
      ...selectCandidate(),
      { type: "AREA_RESPONSE", candidate, response },
    ]);

    const retrying = workflowReducer(enriching, {
      type: "POLL_TRANSIENT_RETRY",
      candidate,
      error: "Backend is waking up",
    });
    expect(retrying).toMatchObject({
      status: "enriching",
      latestArea: response,
      pollFailureCount: 1,
      lastPollError: "Backend is waking up",
    });

    const timedOut = workflowReducer(retrying, {
      type: "POLL_TIMEOUT",
      candidate,
      error: "Enrichment timed out",
    });
    expect(timedOut).toMatchObject({
      status: "error",
      selectedCandidate: candidate,
      latestArea: response,
      errorStage: "poll",
      errorMessage: "Enrichment timed out",
    });
  });

  it("preserves the latest enriching response when retrying the same area", () => {
    const response = areaResponse("enriching");
    const timedOut = reduce([
      ...selectCandidate(),
      { type: "AREA_RESPONSE", candidate, response },
      {
        type: "POLL_TIMEOUT",
        candidate,
        error: "Enrichment timed out",
      },
    ]);

    const retrying = workflowReducer(timedOut, {
      type: "AREA_START",
      candidate,
    });
    expect(retrying).toMatchObject({
      status: "loadingArea",
      selectedCandidate: candidate,
      latestArea: response,
      errorStage: null,
    });
  });

  it("ignores stale geocode and area events", () => {
    const currentSearch = reduce([
      { type: "GEOCODE_START", query: "old" },
      { type: "GEOCODE_START", query: "new" },
    ]);
    const afterOldSearch = workflowReducer(currentSearch, {
      type: "GEOCODE_SUCCESS",
      query: "old",
      candidates: [candidate],
    });
    expect(afterOldSearch).toBe(currentSearch);

    const loadingNewArea = workflowReducer(currentSearch, {
      type: "AREA_START",
      candidate: otherCandidate,
    });
    const afterOldArea = workflowReducer(loadingNewArea, {
      type: "AREA_RESPONSE",
      candidate,
      response: areaResponse("complete"),
    });
    expect(afterOldArea).toBe(loadingNewArea);
  });
});
