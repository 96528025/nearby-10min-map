export const POLL_INITIAL_DELAY_MS = 2_000;
export const POLL_MAX_DELAY_MS = 15_000;
export const MAX_CONSECUTIVE_POLL_FAILURES = 3;

export function configuredDuration(
  value: string | undefined,
  fallback: number,
): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0
    ? Math.floor(parsed)
    : fallback;
}

export const POLL_MAX_DURATION_MS = configuredDuration(
  import.meta.env.VITE_POLL_MAX_DURATION_MS,
  300_000,
);
export const AREA_WAKE_NOTICE_DELAY_MS = configuredDuration(
  import.meta.env.VITE_AREA_WAKE_NOTICE_DELAY_MS,
  5_000,
);
export const AREA_REQUEST_TIMEOUT_MS = configuredDuration(
  import.meta.env.VITE_AREA_REQUEST_TIMEOUT_MS,
  150_000,
);

export function pollDelayMs(attempt: number): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  return Math.min(
    POLL_INITIAL_DELAY_MS * 2 ** safeAttempt,
    POLL_MAX_DELAY_MS,
  );
}
