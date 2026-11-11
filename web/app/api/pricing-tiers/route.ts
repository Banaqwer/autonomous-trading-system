import { handle, jsonOk } from "@/lib/http";
import { PricingTierSchema, type PricingTier } from "@/lib/types";

/**
 * GET /api/pricing-tiers — public.
 * Returns the catalog of subscription tiers. Public so the marketing page and
 * the in-app upgrade flow share one source of truth.
 */
export async function GET() {
  return handle(async () => {
    // SEAM: in production, resolve prices from Stripe (Products/Prices) so the
    // `priceId` values stay authoritative. Static catalog keeps the skeleton
    // runnable without Stripe.
    const tiers: PricingTier[] = PricingTierSchema.array().parse([
      {
        id: "free",
        name: "Explorer",
        priceMonthlyUsd: 0,
        priceId: null,
        features: ["Market data access", "Read-only charts", "Community feed"],
        computeHoursIncluded: 0,
        isCurrent: false,
        cta: "Start free",
      },
      {
        id: "pro",
        name: "Researcher",
        priceMonthlyUsd: 49,
        priceId: process.env.STRIPE_PRICE_ID ?? "price_pro_placeholder",
        features: [
          "Cloud compute",
          "AI research copilot",
          "Backtest run targets",
          "Private projects",
        ],
        computeHoursIncluded: 50,
        isCurrent: false,
        cta: "Upgrade",
      },
      {
        id: "team",
        name: "Team",
        priceMonthlyUsd: 199,
        priceId: "price_team_placeholder",
        features: [
          "Everything in Researcher",
          "Shared projects",
          "Priority compute",
          "Admin console",
        ],
        computeHoursIncluded: 250,
        isCurrent: false,
        cta: "Contact sales",
      },
    ]);

    return jsonOk(tiers);
  });
}
