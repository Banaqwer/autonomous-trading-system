import { PageHeader } from "@/components/page-header";

/**
 * Copilot session view (gated). The chat transcript and composer live here;
 * messages stream from the agent service (AGENT_SERVICE_URL). Slash commands
 * are fetched from /api/slash-commands.
 */
export default function CopilotSessionPage({
  params,
}: {
  params: { sessionId: string };
}) {
  // SEAM: load with `await getCopilotSession(params.sessionId)`.
  return (
    <>
      <PageHeader
        title="Copilot session"
        description={`Session ${params.sessionId}. Chat with the research agent, run slash commands, and let it operate on your projects.`}
      />
      <div className="flex h-[60vh] flex-col rounded-lg border border-white/10 bg-surface">
        <div className="flex-1 overflow-y-auto p-5 text-sm text-white/50">
          {/* SEAM: streamed agent transcript renders here. */}
          Start the conversation — try a slash command like /backtest.
        </div>
        <div className="border-t border-white/10 p-3">
          <div className="rounded-md border border-white/10 px-3 py-2 text-sm text-white/40">
            {/* SEAM: message composer posts to the agent service. */}
            Message the copilot…
          </div>
        </div>
      </div>
    </>
  );
}
