import { getEntitlement, requireUser } from "@/lib/auth";
import { handle, jsonOk } from "@/lib/http";

/**
 * GET /api/is-paid — authenticated.
 * The single source of truth for "does the current user have a paid plan".
 */
export async function GET() {
  return handle(async () => {
    const user = await requireUser();
    // SEAM: entitlement lookup (Stripe-backed).
    const entitlement = await getEntitlement(user.userId);
    return jsonOk(entitlement);
  });
}
