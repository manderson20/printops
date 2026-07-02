export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-zinc-500">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-300 border-t-accent dark:border-zinc-700" />
      {label ?? "Loading…"}
    </div>
  );
}
