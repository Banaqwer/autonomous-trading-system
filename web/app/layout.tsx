import type { Metadata } from "next";

import { ClerkProvider } from "@clerk/nextjs";

import { PostHogProvider } from "@/app/providers";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "OpenQuant",
  description:
    "OpenQuant — an open-source reference architecture for quant research: data, cloud compute, and an AI research copilot.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // SEAM: ClerkProvider reads NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY at runtime.
    <ClerkProvider>
      <html lang="en">
        <body className="min-h-screen bg-canvas text-[#e6e8ee] antialiased">
          {/* SEAM: PostHog analytics provider (client-side init). */}
          <PostHogProvider>{children}</PostHogProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
