import type { AreaResponse, GeocodeCandidate } from "../api/types";

export type WorkflowStatus =
  | "idle"
  | "geocoding"
  | "candidates"
  | "empty"
  | "loadingArea"
  | "enriching"
  | "complete"
  | "osmOnly"
  | "error";

type ReadyWorkflowStatus = Exclude<WorkflowStatus, "error">;

export type WorkflowErrorStage = "default" | "geocode" | "area" | "poll";

interface WorkflowContext {
  readonly query: string;
  readonly candidates: readonly GeocodeCandidate[];
  readonly selectedCandidate: GeocodeCandidate | null;
  readonly latestArea: AreaResponse | null;
  readonly pollFailureCount: number;
  readonly lastPollError: string | null;
}

type ReadyWorkflowState = {
  [Status in ReadyWorkflowStatus]: WorkflowContext & {
    readonly status: Status;
    readonly errorStage: null;
    readonly errorMessage: null;
  };
}[ReadyWorkflowStatus];

type ErrorWorkflowState = WorkflowContext & {
  readonly status: "error";
  readonly errorStage: WorkflowErrorStage;
  readonly errorMessage: string;
};

export type WorkflowState = ReadyWorkflowState | ErrorWorkflowState;

export type WorkflowEvent =
  | { readonly type: "DEFAULT_READY" }
  | { readonly type: "DEFAULT_FAILURE"; readonly error: string }
  | { readonly type: "GEOCODE_START"; readonly query: string }
  | {
      readonly type: "GEOCODE_SUCCESS";
      readonly query: string;
      readonly candidates: readonly GeocodeCandidate[];
    }
  | {
      readonly type: "GEOCODE_FAILURE";
      readonly query: string;
      readonly error: string;
    }
  | { readonly type: "AREA_START"; readonly candidate: GeocodeCandidate }
  | {
      readonly type: "AREA_RESPONSE";
      readonly candidate: GeocodeCandidate;
      readonly response: AreaResponse;
    }
  | {
      readonly type: "AREA_FAILURE";
      readonly candidate: GeocodeCandidate;
      readonly error: string;
    }
  | {
      readonly type: "POLL_TRANSIENT_RETRY";
      readonly candidate: GeocodeCandidate;
      readonly error: string;
    }
  | {
      readonly type: "POLL_TIMEOUT";
      readonly candidate: GeocodeCandidate;
      readonly error: string;
    };

export const initialWorkflowState: WorkflowState = {
  status: "idle",
  query: "",
  candidates: [],
  selectedCandidate: null,
  latestArea: null,
  errorStage: null,
  errorMessage: null,
  pollFailureCount: 0,
  lastPollError: null,
};

function candidatesMatch(
  left: GeocodeCandidate | null,
  right: GeocodeCandidate,
) {
  return (
    left !== null &&
    left.osm === right.osm &&
    left.lat === right.lat &&
    left.lon === right.lon
  );
}

function isDefaultState(state: WorkflowState) {
  return (
    state.status === "idle" ||
    (state.status === "error" && state.errorStage === "default")
  );
}

function isAreaRequestState(state: WorkflowState) {
  return state.status === "loadingArea" || state.status === "enriching";
}

function readyState(
  state: WorkflowState,
  status: ReadyWorkflowStatus,
  changes: Partial<WorkflowContext> = {},
): WorkflowState {
  return {
    ...state,
    ...changes,
    status,
    errorStage: null,
    errorMessage: null,
  };
}

function errorState(
  state: WorkflowState,
  stage: WorkflowErrorStage,
  message: string,
): WorkflowState {
  return {
    ...state,
    status: "error",
    errorStage: stage,
    errorMessage: message,
  };
}

function stateForAreaResponse(
  state: WorkflowState,
  response: AreaResponse,
): WorkflowState {
  const changes: Partial<WorkflowContext> = {
    latestArea: response,
    pollFailureCount: 0,
    lastPollError: null,
  };

  switch (response.status) {
    case "enriching":
      return readyState(state, "enriching", changes);
    case "complete":
      return readyState(state, "complete", changes);
    case "osm_only":
      return readyState(state, "osmOnly", changes);
  }
}

export function workflowReducer(
  state: WorkflowState,
  event: WorkflowEvent,
): WorkflowState {
  switch (event.type) {
    case "DEFAULT_READY":
      return isDefaultState(state) ? readyState(state, "idle") : state;

    case "DEFAULT_FAILURE":
      return isDefaultState(state)
        ? errorState(state, "default", event.error)
        : state;

    case "GEOCODE_START":
      return readyState(state, "geocoding", {
        query: event.query,
        candidates: [],
        pollFailureCount: 0,
        lastPollError: null,
      });

    case "GEOCODE_SUCCESS": {
      if (state.status !== "geocoding" || state.query !== event.query) {
        return state;
      }

      const candidates = [...event.candidates];
      return readyState(
        state,
        candidates.length === 0 ? "empty" : "candidates",
        { candidates },
      );
    }

    case "GEOCODE_FAILURE":
      return state.status === "geocoding" && state.query === event.query
        ? errorState(state, "geocode", event.error)
        : state;

    case "AREA_START": {
      const isRetry = candidatesMatch(state.selectedCandidate, event.candidate);
      return readyState(state, "loadingArea", {
        candidates: [],
        selectedCandidate: event.candidate,
        latestArea: isRetry ? state.latestArea : null,
        pollFailureCount: 0,
        lastPollError: null,
      });
    }

    case "AREA_RESPONSE":
      return isAreaRequestState(state) &&
        candidatesMatch(state.selectedCandidate, event.candidate)
        ? stateForAreaResponse(state, event.response)
        : state;

    case "AREA_FAILURE":
      return state.status === "loadingArea" &&
        candidatesMatch(state.selectedCandidate, event.candidate)
        ? errorState(state, "area", event.error)
        : state;

    case "POLL_TRANSIENT_RETRY":
      return state.status === "enriching" &&
        candidatesMatch(state.selectedCandidate, event.candidate)
        ? readyState(state, "enriching", {
            pollFailureCount: state.pollFailureCount + 1,
            lastPollError: event.error,
          })
        : state;

    case "POLL_TIMEOUT":
      return state.status === "enriching" &&
        candidatesMatch(state.selectedCandidate, event.candidate)
        ? errorState(state, "poll", event.error)
        : state;
  }
}
