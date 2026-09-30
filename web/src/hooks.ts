import { useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { AuditEvent, Plan } from "./types";

/** Opens an SSE stream and hands every JSON message to onMessage (always the latest callback). */
export function useEventSource<T>(url: string | null, onMessage: (data: T) => void, onOpen?: () => void) {
  const handlers = useRef({ onMessage, onOpen });
  handlers.current = { onMessage, onOpen };
  useEffect(() => {
    if (!url) return;
    let es: EventSource, stopped = false, retried = false;
    const open = () => {
      es = new EventSource(url);
      es.onopen = () => { retried = false; handlers.current.onOpen?.(); };
      es.onmessage = (e) => handlers.current.onMessage(JSON.parse(e.data));
      // The browser retries dropped connections itself; an error answer (401: access token expired) closes the
      // stream for good, so refresh once and reopen.
      es.onerror = () => {
        if (es.readyState !== EventSource.CLOSED || stopped || retried) return;
        retried = true;
        // api.me refreshes on 401 and logs out only if that fails; a server that is merely down just stays closed.
        api.me().then(() => { if (!stopped) open(); }, () => {});
      };
    };
    open();
    return () => { stopped = true; es.close(); };
  }, [url]);
}

/**
 * Follows one plan: the audit log (replayed by the server on every (re)connect, then live from a change stream)
 * plus the plan document, reloaded shortly after each burst of events.
 */
export function usePlan(planId: string | null, onEvent: (ev: AuditEvent) => void, onReset: () => void) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const timer = useRef<number>(undefined);

  useEffect(() => {
    setPlan(null);
    setEvents([]);
    return () => clearTimeout(timer.current);
  }, [planId]);

  useEventSource<AuditEvent>(
    planId && `/plans/${planId}/events`,
    (ev) => {
      setEvents((xs) => [...xs, ev]);
      onEvent(ev);
      clearTimeout(timer.current);
      timer.current = window.setTimeout(() => api.plan(planId!).then(setPlan).catch(() => {}), 300);
    },
    () => {
      setEvents([]);
      onReset();
    },
  );
  return { plan, events };
}
