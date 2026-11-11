/**
 * PostHog analytics seam (client + server).
 *
 * The client init is consumed by the `PostHogProvider` in the root layout.
 * The server capture helper is safe to call from route handlers.
 */

import posthog from "posthog-js";

let clientInitialized = false;

/**
 * Initialize the browser PostHog client once. No-ops when the key is absent
 * (e.g. local dev without analytics) or when already initialized.
 */
export function initPostHogClient(): typeof posthog | null {
  if (typeof window === "undefined") return null;
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return null; // SEAM: analytics disabled without a key.
  if (!clientInitialized) {
    posthog.init(key, {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com",
      capture_pageview: false, // captured manually on route changes
      person_profiles: "identified_only",
    });
    clientInitialized = true;
  }
  return posthog;
}

export { posthog };

/**
 * Server-side event capture.
 *
 * SEAM: the real implementation POSTs to the PostHog capture endpoint (or uses
 * posthog-node). Kept as a typed fire-and-forget stub here so route handlers
 * can call it without an extra dependency.
 */
export async function captureServerEvent(
  distinctId: string,
  event: string,
  properties: Record<string, unknown> = {},
): Promise<void> {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return; // analytics disabled
  const host = process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com";
  try {
    // SEAM: replace with posthog-node client for batching/retries.
    await fetch(`${host}/capture/`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        api_key: key,
        event,
        distinct_id: distinctId,
        properties,
      }),
    });
  } catch {
    // Never let analytics failures break a request.
  }
}
