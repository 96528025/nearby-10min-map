import { describe, expect, it } from "vitest";

import {
  POLL_INITIAL_DELAY_MS,
  POLL_MAX_DELAY_MS,
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
