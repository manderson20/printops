import { useState, type ReactNode } from "react";
import { Field, Input } from "@/components/ui/Field";

type Props = {
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
};

/** A password-type input with a Show/Hide toggle — masked secret fields
 * make it impossible to notice a typo or a value pasted into the wrong
 * field, which is exactly the kind of mistake worth letting someone
 * check for themselves before submitting. */
export function PasswordField({ label, value, onChange, placeholder }: Props) {
  const [visible, setVisible] = useState(false);

  return (
    <Field label={label}>
      <div className="relative">
        <Input
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full pr-14"
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          tabIndex={-1}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-xs font-medium text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
        >
          {visible ? "Hide" : "Show"}
        </button>
      </div>
    </Field>
  );
}
