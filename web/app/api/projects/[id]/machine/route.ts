import { z } from "zod";

import { requirePaidUser } from "@/lib/auth";
import { getMachine, setMachineState } from "@/lib/data-client";
import { handle, jsonError, jsonOk } from "@/lib/http";

/**
 * GET  /api/projects/[id]/machine — current machine status for a project.
 * POST /api/projects/[id]/machine — start/stop the project's compute machine.
 * Backed by the engine's machine orchestration.
 */
export async function GET(
  _req: Request,
  { params }: { params: { id: string } },
) {
  return handle(async () => {
    await requirePaidUser();
    // SEAM: engine owns machine lifecycle.
    const machine = await getMachine(params.id);
    return jsonOk(machine);
  });
}

const PostBody = z.object({
  desired: z.enum(["running", "stopped"]),
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
    const machine = await setMachineState(params.id, parsed.data.desired);
    return jsonOk(machine);
  });
}
