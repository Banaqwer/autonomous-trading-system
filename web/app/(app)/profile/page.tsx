import { PageHeader } from "@/components/page-header";
import { requireUser } from "@/lib/auth";

/**
 * Current-user profile & settings (gated). Shows account info, usage, billing
 * entry point (Stripe portal via /api/create-portal-session), and copilot
 * memory settings (/api/memory/settings).
 */
export default async function ProfilePage() {
  const user = await requireUser();

  return (
    <>
      <PageHeader
        title="Profile"
        description="Your account, usage, billing, and copilot memory settings."
      />
      <dl className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-white/10 bg-surface p-5">
          <dt className="text-xs text-white/40">User ID</dt>
          <dd className="mt-1 font-mono text-sm">{user.userId}</dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-surface p-5">
          <dt className="text-xs text-white/40">Email</dt>
          <dd className="mt-1 text-sm">{user.email ?? "—"}</dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-surface p-5">
          <dt className="text-xs text-white/40">Billing</dt>
          <dd className="mt-1 text-sm text-white/60">
            {/* SEAM: button posts to /api/create-portal-session (Stripe). */}
            Manage subscription
          </dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-surface p-5">
          <dt className="text-xs text-white/40">Copilot memory</dt>
          <dd className="mt-1 text-sm text-white/60">
            {/* SEAM: bound to /api/memory/settings. */}
            Configure memory
          </dd>
        </div>
      </dl>
    </>
  );
}
