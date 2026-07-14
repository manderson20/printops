import { useSyncExternalStore } from "react";
import { API_URL } from "@/lib/config";

const TOKEN_KEY = "printops_token";
// Set only while a "View as" session (app/routers/users.py's
// impersonate_user) is active — the admin's own token, stashed aside so
// exitImpersonation can restore it without a fresh login. Its mere
// presence is also how isImpersonating() answers "am I in one of these
// right now", cheaper than decoding the active token's claims client-side
// for that one boolean (the banner itself does read the real claim, via
// useCurrentUser's impersonated_by — this is only for the stash/restore
// mechanics).
const STASHED_ADMIN_TOKEN_KEY = "printops_admin_token_before_impersonation";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

function noopSubscribe(): () => void {
  return () => {};
}

/** Client-only read of the stored auth token, safe to call during render. */
export function useToken(): string | null {
  return useSyncExternalStore(noopSubscribe, getToken, () => null);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function logout(): void {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(STASHED_ADMIN_TOKEN_KEY);
}

/** Swaps in a short-lived "View as" token (from POST
 * /api/v1/users/{id}/impersonate) while stashing the admin's own token
 * aside — see exitImpersonation. Callers must still navigate afterward
 * (e.g. router.push) since useToken only re-renders on the next render a
 * route change already triggers, not on the localStorage write itself. */
export function startImpersonation(token: string): void {
  const adminToken = getToken();
  if (adminToken) {
    window.localStorage.setItem(STASHED_ADMIN_TOKEN_KEY, adminToken);
  }
  setToken(token);
}

export function isImpersonating(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(STASHED_ADMIN_TOKEN_KEY) !== null;
}

/** Restores the admin's own token saved by startImpersonation — falls
 * back to a full logout if it's gone missing for any reason (shouldn't
 * happen, but a broken exit path is worse than a re-login). */
export function exitImpersonation(): void {
  const adminToken = window.localStorage.getItem(STASHED_ADMIN_TOKEN_KEY);
  window.localStorage.removeItem(STASHED_ADMIN_TOKEN_KEY);
  if (adminToken) {
    setToken(adminToken);
  } else {
    logout();
  }
}

export async function login(username: string, password: string): Promise<void> {
  const response = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error("Invalid username or password");
  }
  const data = await response.json();
  setToken(data.access_token);
}

export function startGoogleLogin(): void {
  window.location.href = `${API_URL}/auth/google/login`;
}
