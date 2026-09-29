import { motion } from "motion/react";
import { useEffect, useRef } from "react";

import { describe, isFailure } from "../i18n";
import type { AuditEvent } from "../types";

/** The agent's steps in order: a real sequence, so a rail with one dot per step. */
export default function Timeline({ events }: { events: AuditEvent[] }) {
  const end = useRef<HTMLLIElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [events.length]);  // see Chat: no Promise as cleanup

  return (
    <details className="steps" open>
      <summary>Агентын алхмууд</summary>
      <ol className="timeline">
        {events.map((ev, i) => {
          const [label, detail] = describe(ev);
          return (
            <motion.li key={i} className={`${ev.actor}${isFailure(ev) ? " bad" : ""}`}
              initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.25 }}
              title={JSON.stringify(ev.result ?? ev.args ?? {}).slice(0, 600)}>
              <b>{label}</b>
              {detail && <span> {String(detail).slice(0, 140)}</span>}
              {ev.actor === "agent" && ev.reason && ev.action !== "reply" && <i>{ev.reason.slice(0, 200)}</i>}
            </motion.li>
          );
        })}
        <li ref={end} className="end" aria-hidden />
      </ol>
    </details>
  );
}
