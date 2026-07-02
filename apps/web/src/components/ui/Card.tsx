import { type HTMLAttributes } from "react";

type Props = HTMLAttributes<HTMLDivElement>;

export function Card({ className = "", ...props }: Props) {
  return (
    <div
      className={`rounded-xl border border-black/[.08] bg-white p-6 dark:border-white/[.145] dark:bg-black ${className}`}
      {...props}
    />
  );
}

export function CardTitle({ className = "", ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={`text-sm font-semibold uppercase tracking-wide text-zinc-500 ${className}`}
      {...props}
    />
  );
}
