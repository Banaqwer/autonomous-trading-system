import { z } from "zod";

import { requirePaidUser } from "@/lib/auth";
import { triggerRunTarget } from "@/lib/data-client";
import { handle, jsonError, jsonOk } from "@/lib/http";

/**
 * POST /api/projects/[id]/run-target — trigger a backtest run target.
 * Returns the engine's verdict (Sharpe, drawdown, CAGR, notes).
 */
const PostBody = z.object({
  runTargetId: z.string().min(1),
});

export async function POST(
  req: Request,
  { params }: { params: { id: string } },
) {
  return handle(async () => {
    await requirePaidUser();
    const parsed = PostBody.safeParse(await req.json());
    if (!parsed.success) {
      return jsonError("Invalid request body", 400);
    }
    // SEAM: engine runs the backtest and returns a verdict.
    const verdict = await triggerRunTarget(params.id, parsed.data.runTargetId);
    return jsonOk(verdict);
  });
}
