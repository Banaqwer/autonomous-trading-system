import { requireUser } from "@/lib/auth";
import { handle, jsonOk } from "@/lib/http";
import { UsageSchema, type Usage } from "@/lib/types";

/**
 * GET /api/usage — authenticated.
 * Returns the current billing period's compute + agent token usage.
 */
export async function GET() {
  return handle(async () => {
    const user = await requireUser();
    void user;
    // SEAM: aggregate usage from the engine (compute) and agent service (tokens).
    const usage: Usage = UsageSchema.parse({
      computeHoursUsed: 0,
      computeHoursLimit: 50,
      agentTokensUsed: 0,
      agentTokensLimit: 2_000_000,
      periodStart: new Date().toISOString(),
      periodEnd: new Date().toISOString(),
    });
    return jsonOk(usage);
  });
}
