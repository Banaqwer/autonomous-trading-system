import { z } from "zod";

import { requirePaidUser } from "@/lib/auth";
import { createMemory, listMemory } from "@/lib/agent-client";
import { handle, jsonError, jsonOk } from "@/lib/http";
import { MemoryScopeSchema } from "@/lib/types";

/**
 * GET /api/memory  — list copilot memory entries (optionally by scope).
 * POST /api/memory — create a memory entry.
 * Both require a paid user; delegate to the agent service.
 */
export async function GET(req: Request) {
  return handle(async () => {
    await requirePaidUser();
    const scope = new URL(req.url).searchParams.get("scope") ?? undefined;
    // SEAM: agent service owns memory storage.
    const items = await listMemory(scope);
    return jsonOk(items);
  });
}

const CreateMemoryBody = z.object({
  scope: MemoryScopeSchema,
  content: z.string().min(1),
});

export async function POST(req: Request) {
  return handle(async () => {
    await requirePaidUser();
    const parsed = CreateMemoryBody.safeParse(await req.json());
    if (!parsed.success) {
      return jsonError("Invalid request body", 400);
    }
    const created = await createMemory(parsed.data);
    return jsonOk(created, { status: 201 });
  });
}
