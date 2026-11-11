import { requireUser } from "@/lib/auth";
import { listSlashCommands } from "@/lib/agent-client";
import { handle, jsonOk } from "@/lib/http";

/**
 * GET /api/slash-commands — authenticated.
 * Returns the copilot's available slash commands (fetched from the agent
 * service, with a static fallback so the composer always has something to show).
 */
export async function GET() {
  return handle(async () => {
    await requireUser();
    try {
      // SEAM: agent service is the source of truth for commands.
      const commands = await listSlashCommands();
      return jsonOk(commands);
    } catch {
      // Fallback catalog keeps the UI usable if the agent service is down.
      return jsonOk([
        {
          name: "/backtest",
          description: "Run a backtest run target and return the verdict.",
          usage: "/backtest <run-target>",
          category: "backtest" as const,
        },
        {
          name: "/data",
          description: "Fetch a data series from the engine.",
          usage: "/data <symbol> <range>",
          category: "data" as const,
        },
        {
          name: "/remember",
          description: "Store a fact in copilot memory.",
          usage: "/remember <note>",
          category: "research" as const,
        },
      ]);
    }
  });
}
