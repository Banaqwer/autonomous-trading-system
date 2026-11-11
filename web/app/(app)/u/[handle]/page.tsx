import { PageHeader } from "@/components/page-header";

/**
 * Public-style user profile by handle (gated behind the app shell).
 * Shows another user's display name, bio, and public posts.
 */
export default function UserProfilePage({
  params,
}: {
  params: { handle: string };
}) {
  // SEAM: load the public profile for `params.handle`.
  return (
    <>
      <PageHeader
        title={`@${params.handle}`}
        description="A member's public profile and shared research."
      />
      <div className="rounded-lg border border-white/10 bg-surface p-6 text-sm text-white/60">
        {/* SEAM: render profile + public posts. */}
        No public posts yet.
      </div>
    </>
  );
}
