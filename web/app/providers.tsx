"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { initPostHogClient, posthog } from "@/lib/posthog";

/**
 * PostHog provider stub.
 *
 * Initializes the browser client once and captures a `$pageview` on each
 * client-side navigation. No-ops cleanly when NEXT_PUBLIC_POSTHOG_KEY is unset.
 */
function PageviewTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    const client = initPostHogClient();
    if (!client) return;
    const query = searchParams.toString();
    const url = query ? `${pathname}?${query}` : pathname;
    // SEAM: manual pageview capture (capture_pageview is disabled in init).
    client.capture("$pageview", { $current_url: url });
  }, [pathname, searchParams]);

  return null;
}

export function PostHogProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    initPostHogClient();
    return () => {
      // posthog-js keeps a singleton; nothing to tear down per-mount.
      void posthog;
    };
  }, []);

  return (
    <>
      <Suspense fallback={null}>
        <PageviewTracker />
      </Suspense>
      {children}
    </>
  );
}
