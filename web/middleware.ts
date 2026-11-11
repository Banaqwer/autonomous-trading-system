import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

/**
 * Clerk middleware.
 *
 * Everything is gated by default; routes matched by `isPublicRoute` are open.
 * The public allowlist covers marketing, auth, the data landing page, and
 * legal pages. All app sections and most API routes require a session.
 */
const isPublicRoute = createRouteMatcher([
  "/",
  "/subscribe",
  "/data",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/legal(.*)",
  // Public API surface (reads only, no user data).
  "/api/pricing-tiers",
  // Stub MCP endpoint authenticates via its own token, not Clerk.
  "/api/mcp",
]);

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    // SEAM: Clerk enforces an authenticated session; unauthenticated requests
    // are redirected to sign-in (pages) or rejected (API routes).
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals and static files, run on everything else.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes.
    "/(api|trpc)(.*)",
  ],
};
