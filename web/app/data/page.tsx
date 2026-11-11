import { PageHeader } from "@/components/page-header";

/**
 * Public data landing page. Describes the datasets exposed through the engine's
 * data API. (The authenticated charting/exploration UI lives under /charts.)
 */
export default function DataPage() {
  const datasets = [
    { name: "Equities (daily OHLCV)", coverage: "US listed, 2000–present" },
    { name: "Crypto (minute bars)", coverage: "Top pairs, 2017–present" },
    { name: "Fundamentals", coverage: "Quarterly filings, normalized" },
    { name: "Reference", coverage: "Symbols, calendars, corporate actions" },
  ];

  return (
    <main className="mx-auto max-w-4xl px-4 py-16">
      <PageHeader
        title="Data"
        description="OpenQuant exposes market and reference data through a single typed API served by the engine (DATA_API_URL). Sign in to explore it interactively in Charts."
      />
      <ul className="divide-y divide-white/10 rounded-lg border border-white/10 bg-surface">
        {datasets.map((d) => (
          <li key={d.name} className="flex justify-between px-5 py-4 text-sm">
            <span className="font-medium">{d.name}</span>
            <span className="text-white/50">{d.coverage}</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
