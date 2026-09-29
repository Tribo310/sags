import { useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { AuditEvent, Plan } from "./types";

/** Opens an SSE stream and hands every JSON message to onMessage (always the latest callback). */
export function useEventSource<T>(url: string | null, onMessage: (data: T) => void, onOpen?: () => void) {
  const handlers = useRef({ onMessage, onOpen });
  handlers.current = { onMessage, onOpen };
  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url);
    es.onopen = () => handlers.current.onOpen?.();
    es.onmessage = (e) => handlers.current.onMessage(JSON.parse(e.data));
    return () => es.close();
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
