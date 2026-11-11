import { requireUser } from "@/lib/auth";
import { handle, jsonOk } from "@/lib/http";
import { NotificationSchema, type Notification } from "@/lib/types";

/**
 * GET /api/notifications — authenticated.
 * Returns the current user's notifications (run completions, mentions, billing).
 */
export async function GET() {
  return handle(async () => {
    const user = await requireUser();
    void user;
    // SEAM: aggregate notifications from the engine + social + billing services.
    const notifications: Notification[] = NotificationSchema.array().parse([]);
    return jsonOk(notifications);
  });
}
