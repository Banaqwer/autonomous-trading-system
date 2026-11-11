import { z } from "zod";

import { requirePaidUser } from "@/lib/auth";
import { getMemorySettings, updateMemorySettings } from "@/lib/agent-client";
import { handle, jsonError, jsonOk } from "@/lib/http";

/**
 * GET   /api/memory/settings — read copilot memory settings.
 * PATCH /api/memory/settings — update them.
 */
export async function GET() {
  return handle(async () => {
    await requirePaidUser();
    // SEAM: agent service owns memory settings.
    const settings = await getMemorySettings();
    return jsonOk(settings);
  });
}

const PatchBody = z.object({
  enabled: z.boolean().optional(),
  autoCapture: z.boolean().optional(),
  maxItems: z.number().int().positive().optional(),
});

export async function PATCH(req: Request) {
  return handle(async () => {
    await requirePaidUser();
    const parsed = PatchBody.safeParse(await req.json());
    if (!parsed.success) {
      return jsonError("Invalid request body", 400);
    }
    const updated = await updateMemorySettings(parsed.data);
    return jsonOk(updated);
  });
}
