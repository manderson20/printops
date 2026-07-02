import { type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";

const controlClass =
  "rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50";

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${controlClass} ${className}`} {...props} />;
}

export function Textarea({
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${controlClass} ${className}`} {...props} />;
}

type FieldProps = {
  label: ReactNode;
  children: ReactNode;
  className?: string;
};

export function Field({ label, children, className = "" }: FieldProps) {
  return (
    <label className={`flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300 ${className}`}>
      {label}
      {children}
    </label>
  );
}
