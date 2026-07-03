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
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined);

  useEffect(() => {
    if (!token) {
      setUser(null);
      return;
    }
    getMe()
      .then(setUser)
      .catch(() => setUser(null));
  }, [token]);

  return user;
}
