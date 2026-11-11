import Link from "next/link";

/**
 * Marketing home (public). Introduces the three pillars of the architecture:
 * data, cloud compute, and the AI research copilot.
 */
export default function HomePage() {
  const pillars = [
    {
      title: "Market data",
      body: "Query historical and reference data through a single typed API backed by the engine.",
    },
    {
      title: "Cloud compute",
      body: "Spin up per-project machines to run backtests and research notebooks on demand.",
    },
    {
      title: "AI research copilot",
      body: "An agent that reads your data, runs targets, and reasons about results with you.",
    },
  ];

  return (
    <main className="mx-auto max-w-5xl px-4 py-24">
      <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
        OpenQuant
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-white/70">
        An open-source reference architecture for quant research: data, cloud
        compute, and an AI research copilot — wired together in one workspace.
      </p>

      <div className="mt-10 flex gap-4">
        <Link
          href="/sign-up"
          className="rounded-md bg-accent px-5 py-2.5 text-sm font-medium text-white"
        >
          Get started
        </Link>
        <Link
          href="/subscribe"
          className="rounded-md border border-white/20 px-5 py-2.5 text-sm font-medium"
        >
          See pricing
        </Link>
      </div>

      <section className="mt-20 grid gap-6 sm:grid-cols-3">
        {pillars.map((p) => (
          <div
            key={p.title}
            className="rounded-lg border border-white/10 bg-surface p-6"
          >
            <h2 className="text-lg font-medium">{p.title}</h2>
            <p className="mt-2 text-sm text-white/60">{p.body}</p>
          </div>
        ))}
      </section>
    </main>
  );
}
