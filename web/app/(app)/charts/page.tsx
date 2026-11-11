import { PageHeader } from "@/components/page-header";

/**
 * Charts (gated). Interactive exploration of the engine's market data.
 * A charting client component would fetch series via lib/data-client.
 */
export default function ChartsPage() {
  return (
    <>
      <PageHeader
        title="Charts"
        description="Explore market and reference data interactively. Series are fetched from the engine data API (DATA_API_URL)."
      />
      <div className="flex h-[55vh] items-center justify-center rounded-lg border border-white/10 bg-surface text-sm text-white/40">
        {/* SEAM: mount a client charting component bound to the data API. */}
        Chart canvas
      </div>
    </>
  );
}
