import { redirect } from "next/navigation";

import { PageHeader } from "@/components/page-header";
import { requireUser } from "@/lib/auth";

/**
 * Admin console (gated + role-checked). Only users with the admin role may view
 * it; admin status is resolved via /api/me/admin in a full implementation.
 */
async function isAdmin(userId: string): Promise<boolean> {
  // SEAM: resolve admin role from Clerk org roles or an entitlement service.
  void userId;
  return false;
}

export default async function AdminPage() {
  const user = await requireUser();
  if (!(await isAdmin(user.userId))) {
    // SEAM: non-admins are bounced to the app home.
    redirect("/projects");
  }

  return (
    <>
      <PageHeader
        title="Admin"
        description="Operational console: users, entitlements, and feature flags. Restricted to admins."
      />
      <div className="rounded-lg border border-white/10 bg-surface p-6 text-sm text-white/60">
        {/* SEAM: admin widgets (user search, entitlement overrides, flags). */}
        Admin tools go here.
      </div>
    </>
  );
}
