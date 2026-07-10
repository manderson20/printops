"use client";

import { useEffect, useRef } from "react";
import { refreshSession } from "@/lib/api";
import { setToken } from "@/lib/auth";

const ACTIVITY_EVENTS = ["mousemove", "mousedown", "keydown", "scroll", "touchstart"] as const;

// Short enough to comfortably sit under any reasonable admin-configured
// idle timeout (SessionSettings.idle_timeout_minutes defaults to 60 and
// is unlikely to be set much lower), long enough that this doesn't send a
// refresh call on every scroll pixel or mouse twitch.
const REFRESH_CHECK_INTERVAL_MS = 2 * 60 * 1000;

/** The frontend half of the idle-timeout mechanism (see app/routers/
 * auth.py's /auth/refresh docstring for the other half): while there's
 * been real mouse/keyboard/touch/scroll activity since the last check,
 * periodically reissues the token with a renewed expiry. Stop
 * interacting and this simply stops calling /auth/refresh — the
 * last-issued token's own exp then lapses on its own, and the next API
 * call's 401 sends the user to /login?expired=1 same as always. */
export function useIdleSessionRefresh(): void {
  const activeSinceLastCheck = useRef(false);

  useEffect(() => {
    function markActive() {
      activeSinceLastCheck.current = true;
    }
    ACTIVITY_EVENTS.forEach((event) =>
      window.addEventListener(event, markActive, { passive: true }),
    );

    const interval = setInterval(() => {
      if (!activeSinceLastCheck.current) return;
      activeSinceLastCheck.current = false;
      refreshSession()
        .then((result) => setToken(result.access_token))
        .catch(() => {
          // A failed refresh (idle window already lapsed server-side, or
          // a network hiccup) isn't handled specially here — the next
          // authorizedFetch call naturally 401s and redirects if the
          // session is genuinely gone.
        });
    }, REFRESH_CHECK_INTERVAL_MS);

    return () => {
      ACTIVITY_EVENTS.forEach((event) => window.removeEventListener(event, markActive));
      clearInterval(interval);
    };
  }, []);
}
