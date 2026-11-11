import { auth, currentUser } from "@clerk/nextjs/server";

import { IsPaidSchema, type IsPaid } from "@/lib/types";

/**
 * Authentication + entitlement helpers.
 *
 * `requireUser` is the single choke point every gated route handler calls.
 * It resolves the Clerk-authenticated user or throws `UnauthorizedError`,
 * which the route handler maps to a 401.
 */

export class UnauthorizedError extends Error {
  constructor(message = "Unauthorized") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export class PaymentRequiredError extends Error {
  constructor(message = "Payment required") {
    super(message);
    this.name = "PaymentRequiredError";
  }
}

export interface AuthedUser {
  userId: string;
  email: string | null;
}

/**
 * Resolve the current Clerk user or throw. Call this at the top of every
 * gated route handler and server action.
 */
export async function requireUser(): Promise<AuthedUser> {
  // SEAM: Clerk session resolution. In tests this can be stubbed.
  // Clerk v6: `auth()` is async.
  const { userId } = await auth();
  if (!userId) {
    throw new UnauthorizedError();
  }
  const user = await currentUser();
  return {
    userId,
    email: user?.primaryEmailAddress?.emailAddress ?? null,
  };
}

/**
 * Entitlement check. Delegates to the `is-paid` surface so there is a single
 * source of truth for "does this user have an active paid subscription".
 *
 * SEAM: in production this reads Stripe subscription state (via the billing
 * service or a cached entitlement record). Here it returns a deterministic
 * stub so the skeleton type-checks and runs.
 */
export async function getEntitlement(userId: string): Promise<IsPaid> {
  // SEAM: replace with real Stripe/entitlement lookup keyed by `userId`.
  void userId;
  return IsPaidSchema.parse({
    isPaid: false,
    tierId: null,
    renewsAt: null,
  });
}

/**
 * Require an active paid subscription. Throws `PaymentRequiredError` (map to
 * 402) when the user is authenticated but not entitled.
 */
export async function requirePaidUser(): Promise<AuthedUser> {
  const user = await requireUser();
  const entitlement = await getEntitlement(user.userId);
  if (!entitlement.isPaid) {
    throw new PaymentRequiredError();
  }
  return user;
}

/** Map a thrown auth error to an HTTP status code for route handlers. */
export function authErrorStatus(error: unknown): number | null {
  if (error instanceof UnauthorizedError) return 401;
  if (error instanceof PaymentRequiredError) return 402;
  return null;
}
