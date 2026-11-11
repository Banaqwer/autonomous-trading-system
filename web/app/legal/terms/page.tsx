import { PageHeader } from "@/components/page-header";

/** Terms of Service (public placeholder). */
export default function TermsPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <PageHeader
        title="Terms of Service"
        description="Placeholder terms for the OpenQuant reference project. Replace with your own legal copy before launch."
      />
      <p className="text-sm text-white/60">
        {/* SEAM: insert reviewed legal copy. */}
        OpenQuant is provided as-is under its open-source license.
      </p>
    </main>
  );
}
