import { act, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  area as fetchArea,
  geocode,
  loadDefaultData,
} from "./api/client";
import type { GeocodeResponse } from "./api/types";
import {
  AREA_REQUEST_TIMEOUT_MS,
  AREA_WAKE_NOTICE_DELAY_MS,
  POLL_MAX_DURATION_MS,
} from "./state/polling";
import {
  areaResponse,
  candidate,
  defaultViewData,
  deferred,
} from "./test/fixtures";

vi.mock("./api/client", () => ({
  area: vi.fn(),
  geocode: vi.fn(),
  loadDefaultData: vi.fn(),
}));

vi.mock("./components/AreaMap", () => ({
  AreaMap: ({ centerName }: { centerName: string }) => (
    <div data-testid="area-map">Map for {centerName}</div>
  ),
}));

const areaMock = vi.mocked(fetchArea);
const geocodeMock = vi.mocked(geocode);
const loadDefaultDataMock = vi.mocked(loadDefaultData);

function workflowRoot() {
  return document.querySelector<HTMLElement>("[data-workflow-state]");
}

function submitSearch(query: string) {
  const input = screen.getByLabelText("Destination or attraction");
  fireEvent.change(input, { target: { value: query } });
  const form = input.closest("form");
  if (!form) throw new Error("Search input is not inside a form");
  fireEvent.submit(form);
}

async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function renderReadyApp() {
  const rendered = render(<App />);
  await screen.findByText(/Bundled Apple Park data loaded/);
  return rendered;
}

beforeEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
  loadDefaultDataMock.mockResolvedValue(defaultViewData());
});

afterEach(() => {
  vi.useRealTimers();
});

describe("App workflow", () => {
  it("survives StrictMode's effect setup-cleanup-setup cycle", async () => {
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await screen.findByText(/Bundled Apple Park data loaded/);
    expect(loadDefaultDataMock).toHaveBeenCalledTimes(2);
    expect(loadDefaultDataMock.mock.calls[0]?.[0]?.aborted).toBe(true);
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "idle");
  });

  it("starts idle from bundled data without calling an upstream API", async () => {
    await renderReadyApp();

    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "idle");
    expect(screen.getByRole("heading", { name: "Apple Park" })).toBeInTheDocument();
    expect(screen.getByText("921")).toBeInTheDocument();
    expect(geocodeMock).not.toHaveBeenCalled();
    expect(areaMock).not.toHaveBeenCalled();
  });

  it("exposes OSM, Overture, and Foursquare attribution as page links", async () => {
    await renderReadyApp();

    expect(
      screen.getByRole("link", { name: "OpenStreetMap contributors" }),
    ).toHaveAttribute("href", "https://www.openstreetmap.org/copyright");
    expect(
      screen.getByRole("link", {
        name: "Overture Maps Foundation, overturemaps.org",
      }),
    ).toHaveAttribute("href", "https://overturemaps.org/");
    expect(
      screen.getByRole("link", { name: "Foursquare Places" }),
    ).toHaveAttribute(
      "href",
      "https://opensource.foursquare.com/places-notice-txt/",
    );
  });

  it("renders candidates and the distinct empty terminal state", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock
      .mockResolvedValueOnce({ candidates: [stanford] })
      .mockResolvedValueOnce({ candidates: [] });
    await renderReadyApp();

    submitSearch("Stanford");
    expect(
      await screen.findByRole("button", { name: /Stanford University/ }),
    ).toBeInTheDocument();
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "candidates");

    submitSearch("No such place");
    expect(await screen.findByText(/No matching places found/)).toBeInTheDocument();
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "empty");
  });

  it("shows a readable backend error and retries the failed search", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValueOnce({ candidates: [stanford] });
    await renderReadyApp();

    submitSearch("Stanford");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Search failed: Failed to fetch",
    );
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "error");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("button", { name: /Stanford University/ }),
    ).toBeInTheDocument();
  });

  it("polls with backoff from enriching to complete", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock
      .mockResolvedValueOnce(areaResponse("enriching", stanford, 12))
      .mockResolvedValueOnce(areaResponse("complete", stanford, 42));
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(choice);
      await Promise.resolve();
    });
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "enriching");
    expect(screen.getByText(/OSM facilities are visible now/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_999);
    });
    expect(areaMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(areaMock).toHaveBeenCalledTimes(2);
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "complete");
    expect(screen.getByText(/Facilities complete \(42\)/)).toBeInTheDocument();
  });

  it("renders osm_only as a usable warning rather than success or failure", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockResolvedValue(areaResponse("osm_only", stanford, 12));
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    fireEvent.click(choice);
    expect(await screen.findByText(/当前为 OSM-only 结果/)).toBeInTheDocument();
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "osmOnly");
    expect(screen.getByText("OSM-only")).toBeInTheDocument();
    expect(screen.getByText(/enrichment failed/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("distinguishes configured OSM-only mode from an enrichment failure", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockResolvedValue(
      areaResponse("osm_only", stanford, 12, { enrichError: false }),
    );
    await renderReadyApp();
    submitSearch("Stanford");

    fireEvent.click(
      await screen.findByRole("button", { name: /Stanford University/ }),
    );

    expect(
      await screen.findByText(/enrichment is disabled for this deployment/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/enrichment failed/)).not.toBeInTheDocument();
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "osmOnly");
  });

  it("shows the routed isochrone provenance and modelled area, never a radius, when boundary_mode says routed", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockResolvedValue(
      areaResponse("complete", stanford, 42, {
        boundaryMode: "routed_isochrone",
      }),
    );
    await renderReadyApp();
    submitSearch("Stanford");

    fireEvent.click(
      await screen.findByRole("button", { name: /Stanford University/ }),
    );

    expect(
      await screen.findByText("Routed isochrone (Valhalla, free-flow)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Routed isochrone")).toBeInTheDocument();
    expect(screen.getByText("Modelled area")).toBeInTheDocument();
    expect(screen.getByText("25.8 km²")).toBeInTheDocument();
    expect(
      screen.getByText(/Geometry: Polygon · 1 component · 0 holes/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not establish real-world drive-time accuracy/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Searches attempt to snap to the nearest public drivable road/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/if none is found, they keep the requested point/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Boundary radius/)).not.toBeInTheDocument();
    expect(screen.queryByText("Fixed radius")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Fixed nominal-radius circle"),
    ).not.toBeInTheDocument();
  });

  it("takes the bundled default's provenance from boundary.json instead of inferring it", async () => {
    await renderReadyApp();

    expect(
      screen.getByText("Routed isochrone (Valhalla, free-flow)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Modelled area")).toBeInTheDocument();
    expect(screen.getByText("25.8 km²")).toBeInTheDocument();
    expect(
      screen.getByText(/bundled Apple Park snapshot uses a recorded unsnapped point/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Searches may snap to the nearest public drivable road/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Boundary radius/)).not.toBeInTheDocument();
  });

  it("reports no provenance for a bundled boundary.json that declares no mode", async () => {
    const data = defaultViewData();
    delete data.boundary.boundary_mode;
    loadDefaultDataMock.mockResolvedValue(data);
    await renderReadyApp();

    expect(screen.getByText("Boundary mode not reported")).toBeInTheDocument();
    expect(screen.getByText("Boundary extent")).toBeInTheDocument();
    expect(screen.getByText("Not reported")).toBeInTheDocument();
    expect(screen.queryByText("Modelled area")).not.toBeInTheDocument();
    expect(screen.queryByText("Fixed radius")).not.toBeInTheDocument();
  });

  it("uses nominal boundary_mode despite routed-looking metadata and warns honestly", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockResolvedValue(
      areaResponse("complete", stanford, 42, {
        boundaryMode: "nominal_radius_circle",
        warnings: ["Valhalla was unavailable."],
      }),
    );
    await renderReadyApp();
    submitSearch("Stanford");

    fireEvent.click(
      await screen.findByRole("button", { name: /Stanford University/ }),
    );

    expect(
      await screen.findByText("Fixed nominal-radius circle"),
    ).toBeInTheDocument();
    expect(screen.getByText("Fixed radius · no routing")).toBeInTheDocument();
    expect(screen.getByText("Fixed radius")).toBeInTheDocument();
    expect(screen.getByText("5.0 km")).toBeInTheDocument();
    expect(screen.queryByText("Modelled area")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "当前显示的是固定半径的近似范围，不是基于真实路网计算的约 10 分钟驾车可达范围。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Valhalla was unavailable.")).toBeInTheDocument();
    expect(
      screen.queryByText("Routed isochrone (Valhalla, free-flow)"),
    ).not.toBeInTheDocument();
  });

  it("does not infer provenance when boundary_mode is absent", async () => {
    const stanford = candidate("Stanford University");
    const legacyResponse = areaResponse("complete", stanford, 42);
    delete legacyResponse.boundary_mode;
    delete legacyResponse.boundary.boundary_mode;
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockResolvedValue(legacyResponse);
    await renderReadyApp();
    submitSearch("Stanford");

    fireEvent.click(
      await screen.findByRole("button", { name: /Stanford University/ }),
    );

    expect(
      await screen.findByText("Boundary mode not reported"),
    ).toBeInTheDocument();
    expect(screen.getByText("Boundary extent")).toBeInTheDocument();
    expect(screen.queryByText("Modelled area")).not.toBeInTheDocument();
    expect(screen.queryByText("Fixed radius")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/routed 10-minute drive isochrone \(test fixture\)/),
    ).not.toBeInTheDocument();
  });

  it("shows a delayed wake-up notice while the first area request is pending", async () => {
    const stanford = candidate("Stanford University");
    const firstArea = deferred<ReturnType<typeof areaResponse>>();
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockReturnValue(firstArea.promise);
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    fireEvent.click(choice);
    expect(screen.getByText(/Computing the approximate/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(AREA_WAKE_NOTICE_DELAY_MS - 1);
    });
    expect(screen.queryByText(/service may be waking/)).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByText(/service may be waking/)).toBeInTheDocument();

    await act(async () => {
      firstArea.resolve(areaResponse("complete", stanford, 42));
      await Promise.resolve();
    });
    expect(screen.getByText(/Facilities complete/)).toBeInTheDocument();
    expect(screen.queryByText(/service may be waking/)).not.toBeInTheDocument();
  });

  it("aborts a first area request at its hard timeout and ignores a late response", async () => {
    const stanford = candidate("Stanford University");
    const firstArea = deferred<ReturnType<typeof areaResponse>>();
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockReturnValue(firstArea.promise);
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    fireEvent.click(choice);
    const signal = areaMock.mock.calls[0]?.[1];

    await act(async () => {
      await vi.advanceTimersByTimeAsync(AREA_REQUEST_TIMEOUT_MS);
    });

    expect(signal?.aborted).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /may still be waking after an idle period/,
    );
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "error");

    await act(async () => {
      firstArea.resolve(areaResponse("complete", stanford, 99));
      await Promise.resolve();
    });
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "error");
    expect(screen.queryByText(/Facilities complete \(99\)/)).not.toBeInTheDocument();
  });

  it("cancels first-area wake and deadline timers on unmount", async () => {
    const stanford = candidate("Stanford University");
    const firstArea = deferred<ReturnType<typeof areaResponse>>();
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockReturnValue(firstArea.promise);
    const rendered = await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    fireEvent.click(choice);
    const signal = areaMock.mock.calls[0]?.[1];

    rendered.unmount();
    expect(signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(
        Math.max(AREA_REQUEST_TIMEOUT_MS, AREA_WAKE_NOTICE_DELAY_MS),
      );
      firstArea.resolve(areaResponse("complete", stanford, 99));
      await Promise.resolve();
    });
    expect(areaMock).toHaveBeenCalledTimes(1);
  });

  it("stops after repeated poll failures, keeps OSM data, and can retry", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock
      .mockResolvedValueOnce(areaResponse("enriching", stanford, 12))
      .mockRejectedValueOnce(new Error("service sleeping"))
      .mockRejectedValueOnce(new Error("service sleeping"))
      .mockRejectedValueOnce(new Error("service sleeping"))
      .mockResolvedValueOnce(areaResponse("complete", stanford, 42));
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(choice);
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText(/failed \(1\/3\)/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });
    expect(screen.getByText(/failed \(2\/3\)/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /could not be reached after 3 attempts/,
    );
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "error");
    expect(screen.getByRole("heading", { name: "Stanford University" })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      await Promise.resolve();
    });
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "complete");
  });

  it("turns a long-running enrichment into a retryable timeout", async () => {
    const stanford = candidate("Stanford University");
    const hangingPoll = deferred<ReturnType<typeof areaResponse>>();
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock
      .mockResolvedValueOnce(areaResponse("enriching", stanford, 12))
      .mockReturnValueOnce(hangingPoll.promise);
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(choice);
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(areaMock).toHaveBeenCalledTimes(2);
    const hangingSignal = areaMock.mock.calls[1]?.[1];

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MAX_DURATION_MS - 2_000);
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      /configured time limit/,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "error");
    expect(hangingSignal?.aborted).toBe(true);
  });

  it("starts the polling budget only after the first area response", async () => {
    const stanford = candidate("Stanford University");
    const firstArea = deferred<ReturnType<typeof areaResponse>>();
    const hangingPoll = deferred<ReturnType<typeof areaResponse>>();
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock
      .mockReturnValueOnce(firstArea.promise)
      .mockReturnValueOnce(hangingPoll.promise);
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    fireEvent.click(choice);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100_000);
      firstArea.resolve(areaResponse("enriching", stanford, 12));
      await Promise.resolve();
    });
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "enriching");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(areaMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MAX_DURATION_MS - 2_001);
    });
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "enriching");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /configured time limit/,
    );
  });

  it("does not reschedule a poll that was aborted by a new search", async () => {
    const stanford = candidate("Stanford University");
    const newPlace = candidate("New Place", 37.2, -122.2);
    const inFlightPoll = deferred<ReturnType<typeof areaResponse>>();
    geocodeMock
      .mockResolvedValueOnce({ candidates: [stanford] })
      .mockResolvedValueOnce({ candidates: [newPlace] });
    areaMock
      .mockResolvedValueOnce(areaResponse("enriching", stanford, 12))
      .mockReturnValueOnce(inFlightPoll.promise);
    await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(choice);
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(areaMock).toHaveBeenCalledTimes(2);
    const signal = areaMock.mock.calls[1]?.[1];

    submitSearch("new");
    expect(signal?.aborted).toBe(true);
    inFlightPoll.reject(new DOMException("aborted", "AbortError"));
    await flushPromises();
    await vi.advanceTimersByTimeAsync(POLL_MAX_DURATION_MS);

    expect(areaMock).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole("button", { name: /New Place/ }),
    ).toBeInTheDocument();
  });

  it("aborts and discards an older geocode response", async () => {
    const oldRequest = deferred<GeocodeResponse>();
    const newRequest = deferred<GeocodeResponse>();
    const oldPlace = candidate("Old Place", 37.1, -122.1);
    const newPlace = candidate("New Place", 37.2, -122.2);
    geocodeMock
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise);
    await renderReadyApp();

    submitSearch("old");
    const oldSignal = geocodeMock.mock.calls[0]?.[1];
    submitSearch("new");
    expect(oldSignal?.aborted).toBe(true);

    newRequest.resolve({ candidates: [newPlace] });
    await flushPromises();
    expect(
      screen.getByRole("button", { name: /New Place/ }),
    ).toBeInTheDocument();

    oldRequest.resolve({ candidates: [oldPlace] });
    await flushPromises();
    expect(screen.queryByRole("button", { name: /Old Place/ })).not.toBeInTheDocument();
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "candidates");
  });

  it("prevents an old area request from overwriting a newer selection", async () => {
    const oldPlace = candidate("Old Place", 37.1, -122.1);
    const newPlace = candidate("New Place", 37.2, -122.2);
    const oldArea = deferred<ReturnType<typeof areaResponse>>();
    geocodeMock
      .mockResolvedValueOnce({ candidates: [oldPlace] })
      .mockResolvedValueOnce({ candidates: [newPlace] });
    areaMock
      .mockReturnValueOnce(oldArea.promise)
      .mockResolvedValueOnce(areaResponse("complete", newPlace, 84));
    await renderReadyApp();

    submitSearch("old");
    fireEvent.click(await screen.findByRole("button", { name: /Old Place/ }));
    await flushPromises();
    const oldSignal = areaMock.mock.calls[0]?.[1];

    submitSearch("new");
    expect(oldSignal?.aborted).toBe(true);
    fireEvent.click(await screen.findByRole("button", { name: /New Place/ }));
    expect(
      await screen.findByRole("heading", { name: "New Place" }),
    ).toBeInTheDocument();

    oldArea.resolve(areaResponse("complete", oldPlace, 21));
    await flushPromises();
    expect(screen.getByRole("heading", { name: "New Place" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Old Place" })).not.toBeInTheDocument();
    expect(workflowRoot()).toHaveAttribute("data-workflow-state", "complete");
  });

  it("aborts the active request and stops scheduled polling on unmount", async () => {
    const stanford = candidate("Stanford University");
    geocodeMock.mockResolvedValue({ candidates: [stanford] });
    areaMock.mockResolvedValue(areaResponse("enriching", stanford, 12));
    const rendered = await renderReadyApp();
    submitSearch("Stanford");
    const choice = await screen.findByRole("button", {
      name: /Stanford University/,
    });

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(choice);
      await Promise.resolve();
    });
    const signal = areaMock.mock.calls[0]?.[1];
    rendered.unmount();
    expect(signal?.aborted).toBe(true);

    await vi.advanceTimersByTimeAsync(POLL_MAX_DURATION_MS);
    expect(areaMock).toHaveBeenCalledTimes(1);
  });
});
