export const POLL_INITIAL_DELAY_MS = 2_000;
export const POLL_MAX_DELAY_MS = 15_000;
export const POLL_MAX_DURATION_MS = 120_000;
export const MAX_CONSECUTIVE_POLL_FAILURES = 3;

export function pollDelayMs(attempt: number): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  return Math.min(
    POLL_INITIAL_DELAY_MS * 2 ** safeAttempt,
    POLL_MAX_DELAY_MS,
  );
}
