import { useSyncExternalStore } from "react";
import { API_URL } from "@/lib/config";

const TOKEN_KEY = "printops_token";

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
