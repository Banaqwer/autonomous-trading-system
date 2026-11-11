import { SignUp } from "@clerk/nextjs";

/**
 * Clerk-hosted sign-up (catch-all route). Public.
 */
export default function SignUpPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-16">
      {/* SEAM: Clerk renders the full sign-up flow. */}
      <SignUp />
    </main>
  );
}
