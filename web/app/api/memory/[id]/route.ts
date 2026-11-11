import { requirePaidUser } from "@/lib/auth";
import { deleteMemory } from "@/lib/agent-client";
import { handle, jsonOk } from "@/lib/http";

/**
 * DELETE /api/memory/[id] — delete a single copilot memory entry.
 */
export async function DELETE(
  _req: Request,
  { params }: { params: { id: string } },
) {
  return handle(async () => {
    await requirePaidUser();
    // SEAM: agent service deletes the memory entry.
    const result = await deleteMemory(params.id);
    return jsonOk(result);
  });
}
