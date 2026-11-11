import { z } from "zod";

import { requireUser } from "@/lib/auth";
import { createCopilotSession } from "@/lib/agent-client";
import { handle, jsonError, jsonOk } from "@/lib/http";

/**
 * POST /api/tour/create-qcp-session — authenticated.
 * Creates a Quant Copilot (QCP) session, used by the onboarding tour to drop a
 * new user straight into a guided copilot conversation.
 */
const PostBody = z.object({
  projectId: z.string().nullable().default(null),
  title: z.string().optional(),
});

export async function POST(req: Request) {
  return handle(async () => {
    await requireUser();
    // Body is optional; default to a tour session with no project.
    let body: unknown = {};
    try {
      body = await req.json();
    } catch {
      body = {};
    }
    const parsed = PostBody.safeParse(body);
    if (!parsed.success) {
      return jsonError("Invalid request body", 400);
    }
    // SEAM: agent service creates the copilot session.
    const session = await createCopilotSession({
      projectId: parsed.data.projectId,
      title: parsed.data.title ?? "Guided tour",
    });
    return jsonOk(session, { status: 201 });
  });
}
