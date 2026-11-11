import Link from "next/link";

import { PageHeader } from "@/components/page-header";

/**
 * Social feed (gated). A feed of shared research posts, each optionally carrying
 * an engine verdict. Individual posts live at /social/[id].
 */
export default function SocialPage() {
  // SEAM: load feed from the engine/social service.
  const posts = [
    { id: "post_1", title: "SPY mean reversion holds out-of-sample", author: "ada" },
    { id: "post_2", title: "Momentum decay after earnings", author: "lin" },
  ];

  return (
    <>
      <PageHeader
        title="Social"
        description="A feed of shared research write-ups from the community. Posts can attach a verifiable backtest verdict."
      />
      <ul className="space-y-4">
        {posts.map((p) => (
          <li
            key={p.id}
            className="rounded-lg border border-white/10 bg-surface p-5"
          >
            <Link href={`/social/${p.id}`} className="font-medium hover:underline">
              {p.title}
            </Link>
            <div className="mt-1 text-xs text-white/40">
              by{" "}
              <Link href={`/u/${p.author}`} className="hover:underline">
                @{p.author}
              </Link>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
