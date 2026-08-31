import { describe, expect, it } from "vitest";

import {
  AREA_REQUEST_TIMEOUT_MS,
  AREA_WAKE_NOTICE_DELAY_MS,
  POLL_INITIAL_DELAY_MS,
  POLL_MAX_DELAY_MS,
  POLL_MAX_DURATION_MS,
  configuredDuration,
  pollDelayMs,
} from "./polling";

describe("pollDelayMs", () => {
  it("backs off exponentially instead of polling at a fixed interval", () => {
    expect([0, 1, 2, 3].map(pollDelayMs)).toEqual([
      POLL_INITIAL_DELAY_MS,
      4_000,
      8_000,
      POLL_MAX_DELAY_MS,
    ]);
  });

  it("caps long-running polling and sanitizes invalid attempts", () => {
    expect(pollDelayMs(20)).toBe(POLL_MAX_DELAY_MS);
    expect(pollDelayMs(-2)).toBe(POLL_INITIAL_DELAY_MS);
  });
});

describe("duration configuration", () => {
  it("uses Render-safe defaults when Vite overrides are absent", () => {
    expect(configuredDuration(undefined, 300_000)).toBe(300_000);
    expect(configuredDuration(undefined, 5_000)).toBe(5_000);
    expect(configuredDuration(undefined, 150_000)).toBe(150_000);

    expect(POLL_MAX_DURATION_MS).toBe(
      configuredDuration(import.meta.env.VITE_POLL_MAX_DURATION_MS, 300_000),
    );
    expect(AREA_WAKE_NOTICE_DELAY_MS).toBe(
      configuredDuration(
        import.meta.env.VITE_AREA_WAKE_NOTICE_DELAY_MS,
        5_000,
      ),
    );
    expect(AREA_REQUEST_TIMEOUT_MS).toBe(
      configuredDuration(
        import.meta.env.VITE_AREA_REQUEST_TIMEOUT_MS,
        150_000,
      ),
    );
  });

  it("accepts positive millisecond overrides and rejects invalid values", () => {
    expect(configuredDuration("45000", 10)).toBe(45_000);
    expect(configuredDuration("1200.9", 10)).toBe(1_200);
    expect(configuredDuration("0", 10)).toBe(10);
    expect(configuredDuration("not-a-number", 10)).toBe(10);
  });
});
