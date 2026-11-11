import { PageHeader } from "@/components/page-header";

/** Privacy Policy (public placeholder). */
export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <PageHeader
        title="Privacy Policy"
        description="Placeholder privacy policy for the OpenQuant reference project. Replace with your own before launch."
      />
      <p className="text-sm text-white/60">
        {/* SEAM: insert reviewed privacy copy; note analytics (PostHog) usage. */}
        Describe what data is collected and how it is used.
      </p>
    </main>
  );
}
