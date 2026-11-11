import { requireUser } from "@/lib/auth";
import { handle, jsonOk } from "@/lib/http";
import { AdminStatusSchema, type AdminStatus } from "@/lib/types";

/**
 * GET /api/me/admin — authenticated.
 * Reports whether the current user has the admin role (and which roles).
 */
export async function GET() {
  return handle(async () => {
    const user = await requireUser();
    // SEAM: resolve admin role from Clerk org roles or an entitlement service.
    void user;
    const status: AdminStatus = AdminStatusSchema.parse({
      isAdmin: false,
      roles: [],
    });
    return jsonOk(status);
  });
}
