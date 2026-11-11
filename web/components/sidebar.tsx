import Link from "next/link";

/**
 * App shell sidebar navigation. Rendered inside the gated `(app)` layout.
 * Links mirror the primary product sections.
 */
const NAV: { label: string; href: string; hint: string }[] = [
  { label: "Copilot", href: "/quant-copilot", hint: "AI research agent" },
  { label: "Projects", href: "/projects", hint: "Compute + backtests" },
  { label: "Charts", href: "/charts", hint: "Market data charts" },
  { label: "Social", href: "/social", hint: "Shared research feed" },
  { label: "You", href: "/profile", hint: "Profile & settings" },
];

export function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-white/10 bg-surface p-4">
      <Link href="/projects" className="mb-8 block px-2 text-lg font-semibold">
        OpenQuant
      </Link>
      <nav className="flex flex-col gap-1">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="group rounded-md px-3 py-2 text-sm text-white/80 hover:bg-white/5 hover:text-white"
          >
            <span className="font-medium">{item.label}</span>
            <span className="block text-xs text-white/40 group-hover:text-white/60">
              {item.hint}
            </span>
          </Link>
        ))}
      </nav>
      <div className="mt-auto pt-6 text-xs text-white/30">
        {/* SEAM: Clerk <UserButton /> and usage meter can mount here. */}
        Signed in
      </div>
    </aside>
  );
}
