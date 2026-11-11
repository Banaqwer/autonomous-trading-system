import Link from "next/link";

import { PageHeader } from "@/components/page-header";

/**
 * Legal index (public). Terms and privacy stubs live here. These pages are in
 * the middleware public allowlist.
 */
export default function LegalPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <PageHeader
        title="Legal"
        description="Terms of service and privacy policy for OpenQuant."
      />
      <ul className="space-y-2 text-sm text-white/70">
        <li>
          <Link href="/legal/terms" className="hover:underline">
            Terms of Service
          </Link>
        </li>
        <li>
          <Link href="/legal/privacy" className="hover:underline">
            Privacy Policy
          </Link>
        </li>
      </ul>
    </main>
  );
}
