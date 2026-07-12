import { type ReactNode } from "react";

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="text-sm text-zinc-500">{children}</p>;
}

export function ErrorState({ children }: { children: ReactNode }) {
  return <p className="text-sm text-red-600 dark:text-red-400">{children}</p>;
}

export function SuccessState({ children }: { children: ReactNode }) {
  return <p className="text-sm text-green-600 dark:text-green-400">{children}</p>;
}
