import Link from "next/link";

import { PageHeader } from "@/components/page-header";

/**
 * Projects list (gated). Each project owns a compute machine and a set of run
 * targets (strategies/notebooks). Data is loaded from the engine via
 * `lib/data-client` in a full implementation.
 */
export default function ProjectsPage() {
  // SEAM: replace with `await listProjects()` from lib/data-client once the
  // engine is reachable. Static sample keeps the skeleton renderable.
  const projects = [
    { id: "proj_demo", name: "Mean reversion research" },
    { id: "proj_momentum", name: "Cross-sectional momentum" },
  ];

  return (
    <>
      <PageHeader
        title="Projects"
        description="Your research projects. Each project has its own cloud machine and backtest run targets served by the engine."
      />
      <ul className="grid gap-4 sm:grid-cols-2">
        {projects.map((p) => (
          <li key={p.id}>
            <Link
              href={`/projects/${p.id}`}
              className="block rounded-lg border border-white/10 bg-surface p-5 hover:border-white/25"
            >
              <span className="font-medium">{p.name}</span>
              <span className="mt-1 block text-xs text-white/40">{p.id}</span>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
