/**
 * Small presentational helper used by every page to render a consistent
 * heading + short description block.
 */
export function PageHeader({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <header className="mb-8 border-b border-white/10 pb-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 max-w-2xl text-sm text-white/60">{description}</p>
    </header>
  );
}
