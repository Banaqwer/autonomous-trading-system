import { PageHeader } from "@/components/page-header";

/**
 * Social post detail (gated). Shows a single research write-up, its attached
 * verdict, and discussion.
 */
export default function SocialPostPage({
  params,
}: {
  params: { id: string };
}) {
  // SEAM: load the post (and its verdict) from the engine/social service.
  return (
    <>
      <PageHeader
        title="Research post"
        description={`Post ${params.id}. The author's write-up, attached backtest verdict, and comments.`}
      />
      <article className="rounded-lg border border-white/10 bg-surface p-6 text-sm text-white/60">
        {/* SEAM: render post body + verdict badge. */}
        Post content loads here.
      </article>
    </>
  );
}
