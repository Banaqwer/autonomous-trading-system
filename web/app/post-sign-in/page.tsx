import { redirect } from "next/navigation";

import { getEntitlement, requireUser } from "@/lib/auth";

/**
 * Post-sign-in router. Clerk redirects here after authentication; we decide
 * where to send the user based on entitlement:
 *   - paid  -> /projects (the app)
 *   - unpaid -> /subscribe (upgrade)
 */
export default async function PostSignInPage() {
  const user = await requireUser();
  const entitlement = await getEntitlement(user.userId);

  // SEAM: entitlement drives the first-run destination.
  if (entitlement.isPaid) {
    redirect("/projects");
  }
  redirect("/subscribe");
}
