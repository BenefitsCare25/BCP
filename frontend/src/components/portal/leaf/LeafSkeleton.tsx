/** Loading state for a leaf.
 *
 * Empty mounts, drawn in the frame ink — the page's own structure arriving
 * before its contents, which is what an album leaf literally is. The wrapper
 * carries `role="status"` and a named label, so a screen reader is told the
 * page is loading instead of meeting a dozen silent `aria-hidden` blocks. */
export function LeafSkeleton({
  label = "Loading",
  mounts = 3,
}: {
  label?: string;
  mounts?: number;
}) {
  return (
    <div role="status" aria-live="polite" className="space-y-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: mounts }, (_, i) => (
        <div
          key={i}
          aria-hidden
          className="rounded-tile border border-glass-edge bg-glass p-4 shadow-mount backdrop-blur-glass sm:p-5"
        >
          <div className="h-3 w-1/3 animate-pulse rounded-pill bg-track" />
          <div className="mt-2 h-3 w-2/3 animate-pulse rounded-pill bg-track" />
          <div className="mt-5 h-2.5 w-full animate-pulse rounded-pill bg-track" />
          <div className="mt-3 h-3 w-1/2 animate-pulse rounded-pill bg-track" />
        </div>
      ))}
    </div>
  );
}
