import { requireUser } from "@/lib/auth";
import { handle, jsonOk } from "@/lib/http";
import { createPortalSession } from "@/lib/stripe";

/**
 * POST /api/create-portal-session — authenticated.
 * Creates a Stripe Billing Portal session and returns its URL for redirect.
 */
export async function POST() {
  return handle(async () => {
    const user = await requireUser();

    // SEAM: look up the Stripe customer id for this user. Using the Clerk user
    // id as a stand-in keeps the stub typed.
    const customerId = `cus_${user.userId}`;
    const returnUrl = `${process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"}/profile`;

    const session = await createPortalSession({ customerId, returnUrl });
    return jsonOk(session);
  });
}
