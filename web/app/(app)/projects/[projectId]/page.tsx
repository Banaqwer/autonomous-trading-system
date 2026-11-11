import { PageHeader } from "@/components/page-header";

/**
 * Project detail (gated). Shows the project's machine controls and run targets.
 * Machine + run-target actions go through the API routes under
 * /api/projects/[id]/machine and /api/projects/[id]/run-target.
 */
export default function ProjectDetailPage({
  params,
}: {
  params: { projectId: string };
}) {
  // SEAM: load with `await getProject(params.projectId)` and
  // `await listRunTargets(params.projectId)` from lib/data-client.
  return (
    <>
      <PageHeader
        title={`Project ${params.projectId}`}
        description="Start or stop the project's compute machine and trigger backtest run targets. Results return an engine verdict (Sharpe, drawdown, CAGR)."
      />

      <section className="mb-8 rounded-lg border border-white/10 bg-surface p-5">
        <h2 className="text-sm font-medium text-white/70">Machine</h2>
        <p className="mt-1 text-xs text-white/40">
          {/* SEAM: wire to /api/projects/[id]/machine */}
          Status: stopped — start to run backtests.
        </p>
      </section>

      <section className="rounded-lg border border-white/10 bg-surface p-5">
        <h2 className="text-sm font-medium text-white/70">Run targets</h2>
        <p className="mt-1 text-xs text-white/40">
          {/* SEAM: wire to /api/projects/[id]/run-target */}
          No run targets yet.
        </p>
      </section>
    </>
  );
}
