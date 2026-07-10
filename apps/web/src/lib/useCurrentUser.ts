import { useEffect, useState } from "react";
import { getMe, type CurrentUser } from "@/lib/api";
import { useToken } from "@/lib/auth";

/** Loads the signed-in user (and role) once a token is present.
 * `undefined` = still loading (no token yet, or /auth/me in flight) —
 * distinct from `null`, which means confirmed logged out/invalid token.
 * Callers that redirect on role should wait past `undefined` first, or
 * they'll bounce a real admin before the fetch resolves. */
export function useCurrentUser(): CurrentUser | null | undefined {
  const token = useToken();
  const [fetchedUser, setFetchedUser] = useState<CurrentUser | null | undefined>(undefined);
  const [prevToken, setPrevToken] = useState(token);

  // Reset to "loading" the instant the token identity changes (login or
  // logout), computed during render rather than via an effect + setState —
  // this is what React's own docs recommend for "adjust state when a prop
  // changes" instead of letting a stale previous user's data show through
  // for a render or two while the new fetch is in flight.
  if (token !== prevToken) {
    setPrevToken(token);
    setFetchedUser(undefined);
  }

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getMe()
      .then((u) => {
        if (!cancelled) setFetchedUser(u);
      })
      .catch(() => {
        if (!cancelled) setFetchedUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return token ? fetchedUser : null;
}
