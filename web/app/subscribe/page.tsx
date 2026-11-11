import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { PricingTierSchema, type PricingTier } from "@/lib/types";

/**
 * Public pricing page. Reads the tiers from the `pricing-tiers` API so the
 * marketing page and the in-app upgrade flow share one source of truth.
 */
async function loadTiers(): Promise<PricingTier[]> {
  const base = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  try {
    const res = await fetch(`${base}/api/pricing-tiers`, { cache: "no-store" });
    if (!res.ok) return [];
    const json = (await res.json()) as unknown;
    return PricingTierSchema.array().parse(json);
  } catch {
    // SEAM: during build or without a running server, render no tiers.
    return [];
  }
}

export default async function SubscribePage() {
  const tiers = await loadTiers();

  return (
    <main className="mx-auto max-w-5xl px-4 py-16">
      <PageHeader
        title="Pricing"
        description="Choose a plan. Paid plans unlock cloud compute and the AI research copilot. Checkout runs through Stripe."
      />

      <div className="grid gap-6 sm:grid-cols-3">
        {tiers.length === 0 ? (
          <p className="text-sm text-white/50">
            Pricing is loading or unavailable. (SEAM: configure the pricing-tiers
            API / Stripe.)
          </p>
        ) : (
          tiers.map((tier) => (
            <div
              key={tier.id}
              className="flex flex-col rounded-lg border border-white/10 bg-surface p-6"
            >
              <h2 className="text-lg font-medium">{tier.name}</h2>
              <p className="mt-2 text-3xl font-semibold">
                ${tier.priceMonthlyUsd}
                <span className="text-sm font-normal text-white/50">/mo</span>
              </p>
              <ul className="mt-4 flex-1 space-y-1 text-sm text-white/60">
                {tier.features.map((f) => (
                  <li key={f}>• {f}</li>
                ))}
              </ul>
              <Link
                href="/sign-up"
                className="mt-6 rounded-md bg-accent px-4 py-2 text-center text-sm font-medium text-white"
              >
                {tier.cta}
              </Link>
            </div>
          ))
        )}
      </div>
    </main>
  );
}
