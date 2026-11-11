import { Sidebar } from "@/components/sidebar";
import { requireUser } from "@/lib/auth";

/**
 * Gated app shell. Every route in the `(app)` group renders inside this
 * sidebar layout. Middleware already enforces authentication; `requireUser`
 * here is a defense-in-depth check and gives server components the user id.
 */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // SEAM: throws -> Clerk middleware/redirect handles unauthenticated access.
  await requireUser();

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto px-8 py-10">{children}</main>
    </div>
  );
}
