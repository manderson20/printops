"use client";

import { Suspense, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { login, startGoogleLogin } from "@/lib/auth";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const expired = searchParams.get("expired") === "1";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      router.push("/printers");
    } catch {
      setError("Invalid username or password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <div className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-black/[.08] bg-white p-8 dark:border-white/[.145] dark:bg-black">
        <div className="mb-2 flex flex-col items-center gap-2 text-center">
          <Image src="/printops-logo.png" alt="PrintOps" width={56} height={56} priority />
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Sign in to PrintOps</h1>
          <p className="text-xs text-zinc-500">Print management. Simplified.</p>
        </div>

        {expired && <ErrorState>Your session expired. Please sign in again.</ErrorState>}

        <Button type="button" variant="secondary" onClick={startGoogleLogin}>
          Sign in with Google
        </Button>

        <details className="group text-sm">
          <summary className="cursor-pointer list-none text-center text-xs text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300">
            Use a local account instead
          </summary>

          <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-4">
            <Field label="Username">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </Field>

            <Field label="Password">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </Field>

            {error && <ErrorState>{error}</ErrorState>}

            <Button type="submit" variant="secondary" disabled={submitting}>
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </details>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
