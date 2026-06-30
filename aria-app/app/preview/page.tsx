import Link from 'next/link';

// Preview index — lists boilerplate-grade rebuilt pages for operator review. Served by
// aria-app (excluded from the aria-web proxy); the live platform is unaffected.
export default function PreviewIndex() {
  return (
    <div className="mx-auto max-w-2xl p-10">
      <h1 className="text-2xl font-semibold tracking-tight">ARIA — rebuild preview</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        New boilerplate-grade pages for review. The live platform is unchanged.
      </p>
      <ul className="mt-6 space-y-2">
        <li>
          <Link href="/preview/bd" className="text-primary hover:underline">
            → Business Development Intelligence (rebuilt)
          </Link>
        </li>
      </ul>
    </div>
  );
}
