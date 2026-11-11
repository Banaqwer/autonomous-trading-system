import Link from "next/link";

import { PageHeader } from "@/components/page-header";

/**
 * Quant Copilot home (gated). Lists recent agent sessions and starts new ones.
 * Sessions are created via the agent service (lib/agent-client) /
 * /api/tour/create-qcp-session.
 */
export default function QuantCopilotPage() {
  // SEAM: list sessions from the agent service.
  const sessions = [
    { id: "sess_welcome", title: "Getting started" },
  ];

  return (
    <>
      <PageHeader
        title="Quant Copilot"
        description="An AI research agent that reads your data, runs backtests, and reasons about results. Supports slash commands and persistent memory."
      />
      <div className="mb-6">
        <Link
          href="/quant-copilot/sess_welcome"
          className="inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          New session
        </Link>
      </div>
      <ul className="divide-y divide-white/10 rounded-lg border border-white/10 bg-surface">
        {sessions.map((s) => (
          <li key={s.id}>
            <Link
              href={`/quant-copilot/${s.id}`}
              className="block px-5 py-4 text-sm hover:bg-white/5"
            >
              {s.title}
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
