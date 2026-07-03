"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { setToken } from "@/lib/auth";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

export default function LoginCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The backend redirects here with the outcome in a URL *fragment*
    // (never a query param) so a session token never ends up in server
    // logs or the Referer header — see app/routers/auth.py's /auth/google/callback.
    // Deliberately under /login, not /auth — Caddy proxies /auth/* entirely
    // to the API, so a page at /auth/callback is unreachable.
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("token");
    const errorMessage = params.get("error");
    history.replaceState(null, "", window.location.pathname);

    if (token) {
      setToken(token);
      router.replace("/printers");
    } else {
      setError(errorMessage ?? "Sign-in failed.");
    }
  }, [router]);

  if (error) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 bg-zinc-50 p-8 text-center dark:bg-black">
        <ErrorState>{error}</ErrorState>
        <a href="/login" className="text-sm text-accent hover:underline">
          Back to sign in
        </a>
      </div>
    );
  }

  return (
    <div className="flex flex-1 items-center justify-center bg-zinc-50 dark:bg-black">
      <Spinner label="Signing in…" />
    </div>
  );
}
