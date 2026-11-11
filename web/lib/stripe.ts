/**
 * Stripe billing seam.
 *
 * The real implementation instantiates the Stripe SDK with STRIPE_SECRET_KEY
 * and calls the Checkout / Billing Portal APIs. To keep the skeleton dependency
 * -free (and type-checking without the `stripe` package installed), this module
 * exposes the same surface backed by typed stubs. Swap the bodies for real
 * Stripe calls when credentials are wired up.
 */

export interface CheckoutSessionResult {
  url: string;
  sessionId: string;
}

export interface PortalSessionResult {
  url: string;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    // SEAM: surfaces a clear error until Stripe credentials are set.
    throw new Error(`Missing required env var: ${name}`);
  }
  return value;
}

/**
 * Create a Stripe Checkout session for the configured subscription price.
 *
 * SEAM: replace body with:
 *   const stripe = new Stripe(requireEnv("STRIPE_SECRET_KEY"));
 *   const session = await stripe.checkout.sessions.create({ ... });
 */
export async function createCheckoutSession(params: {
  customerEmail: string | null;
  successUrl: string;
  cancelUrl: string;
}): Promise<CheckoutSessionResult> {
  const priceId = requireEnv("STRIPE_PRICE_ID");
  // SEAM: real Stripe Checkout call goes here.
  return {
    url: `${params.successUrl}?stub_checkout=1&price=${encodeURIComponent(priceId)}`,
    sessionId: "cs_stub_session",
  };
}

/**
 * Create a Stripe Billing Portal session so a subscriber can manage billing.
 *
 * SEAM: replace body with:
 *   const stripe = new Stripe(requireEnv("STRIPE_SECRET_KEY"));
 *   const session = await stripe.billingPortal.sessions.create({ customer, return_url });
 */
export async function createPortalSession(params: {
  customerId: string;
  returnUrl: string;
}): Promise<PortalSessionResult> {
  requireEnv("STRIPE_SECRET_KEY");
  // SEAM: real Stripe Billing Portal call goes here.
  return {
    url: `${params.returnUrl}?stub_portal=1&customer=${encodeURIComponent(params.customerId)}`,
  };
}
