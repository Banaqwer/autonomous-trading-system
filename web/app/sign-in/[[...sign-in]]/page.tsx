import { SignIn } from "@clerk/nextjs";

/**
 * Clerk-hosted sign-in (catch-all route). Public.
 */
export default function SignInPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-16">
      {/* SEAM: Clerk renders the full sign-in flow. */}
      <SignIn />
    </main>
  );
}
