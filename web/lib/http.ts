import { NextResponse } from "next/server";

import { authErrorStatus } from "@/lib/auth";

/**
 * Route-handler helpers: consistent typed JSON responses and a wrapper that
 * maps thrown auth/entitlement errors to the right HTTP status.
 */

export function jsonOk<T>(data: T, init?: ResponseInit): NextResponse {
  return NextResponse.json(data, { status: 200, ...init });
}

export function jsonError(
  message: string,
  status: number,
): NextResponse<{ error: string }> {
  return NextResponse.json({ error: message }, { status });
}

/**
 * Run a route handler body, converting known auth errors into 401/402 and
 * anything else into a 500. Keeps each handler focused on its happy path.
 */
export async function handle(
  fn: () => Promise<NextResponse>,
): Promise<NextResponse> {
  try {
    return await fn();
  } catch (error) {
    const status = authErrorStatus(error);
    if (status) {
      return jsonError(
        status === 402 ? "Payment required" : "Unauthorized",
        status,
      );
    }
    const message =
      error instanceof Error ? error.message : "Internal server error";
    // SEAM: wire structured logging / error reporting here.
    return jsonError(message, 500);
  }
}
