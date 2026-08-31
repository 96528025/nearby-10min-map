import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, area, buildAreaPath, geocode } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("buildAreaPath", () => {
  it("rounds both coordinates to the backend cache's four-decimal precision", () => {
    const path = buildAreaPath({
      lat: 37.3318249,
      lon: -122.0311806,
      name: "Apple Park & Visitor Center",
    });

    const url = new URL(path, "https://example.test");
    expect(url.pathname).toBe("/api/area");
    expect(url.searchParams.get("lat")).toBe("37.3318");
    expect(url.searchParams.get("lon")).toBe("-122.0312");
    expect(url.searchParams.get("name")).toBe("Apple Park & Visitor Center");
  });

  it("retains trailing zeroes so cache keys always use exactly four places", () => {
    const path = buildAreaPath({ lat: 40.7, lon: -74 });
    const url = new URL(path, "https://example.test");

    expect(url.searchParams.get("lat")).toBe("40.7000");
    expect(url.searchParams.get("lon")).toBe("-74.0000");
  });
});

describe("request handling", () => {
  it("uses the rounded, same-origin area path and forwards the AbortSignal", async () => {
    const response = {
      status: "complete",
      name: "Apple Park",
      lat: 37.3318,
      lon: -122.0312,
      boundary: {},
      facilities: {},
      total: 0,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await area(
      { lat: 37.3318249, lon: -122.0311806, name: "Apple Park" },
      controller.signal,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/area?lat=37.3318&lon=-122.0312&name=Apple+Park",
      { signal: controller.signal },
    );
  });

  it("turns a successful HTML catch-all response into a readable ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><title>App</title>", {
          status: 200,
          headers: { "content-type": "text/html; charset=utf-8" },
        }),
      ),
    );

    const request = geocode({ query: "Cupertino" });

    await expect(request).rejects.toMatchObject({
      name: "ApiError",
      status: 200,
      message: expect.stringContaining(
        "expected JSON but received text/html; charset=utf-8",
      ),
    });
    await expect(request).rejects.toBeInstanceOf(ApiError);
  });

  it("preserves FastAPI's detail message for JSON error responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Address query is required" }), {
          status: 422,
          statusText: "Unprocessable Entity",
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(geocode({ query: "" })).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      message: "Address query is required",
    });
  });

  it("reports malformed JSON as an ApiError instead of leaking SyntaxError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("{not-json", {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(geocode({ query: "Cupertino" })).rejects.toMatchObject({
      name: "ApiError",
      status: 200,
      message: "The server returned invalid JSON",
    });
  });
});
