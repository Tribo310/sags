import { motion } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";

import { agentName, describe, isFailure } from "../i18n";
import type { AuditEvent } from "../types";

/** Dot colour of a step: done ok, the running one blue, waiting on the household amber, system mute. */
function kind(ev: AuditEvent, last: boolean, running: boolean) {
  if (isFailure(ev)) return "bad";
  if (last && running) return "active";
  if (last && (ev.action === "ask" || (ev.action === "state" && ev.result?.to === "awaiting_approval"))) return "wait";
  return ev.actor === "system" ? "sys" : "done";
}

/** Every agent's ReAct steps, folded into the chat turn: collapsed to the latest step, open while the run works. */
export default function Timeline({ events, running }: { events: AuditEvent[]; running: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);  // null: follow the run
  const shown = open ?? running;
  const id = useId();
  const end = useRef<HTMLLIElement>(null);
  useEffect(() => { if (running) setOpen(null); }, [running]);
  useEffect(() => { if (shown) end.current?.scrollIntoView({ block: "nearest" }); }, [events.length, shown]);  // block body: see Chat

  if (!events.length) return null;
  const last = events[events.length - 1];
  const [lastLabel, lastDetail] = describe(last);
  return (
    <div className="steps">
      <button type="button" aria-expanded={shown} aria-controls={id} onClick={() => setOpen(!shown)}>
        <span className={`kdot k-${kind(last, true, running)}`} />
        <span className="t">Агентын алхмууд</span>
        <span className="count num">{events.length}</span>
        <span className="last">{shown ? "" : `${lastLabel} ${lastDetail ?? ""}`}</span>
        <span className="toggle">{shown ? "Хураах" : "Дэлгэх"}</span>
      </button>
      {shown && (
        <ol id={id}>
          {events.map((ev, i) => {
            const [label, detail] = describe(ev);
            return (
              <motion.li key={i} ref={i === events.length - 1 ? end : undefined}
                initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.2 }}
                title={JSON.stringify(ev.result ?? ev.args ?? {}).slice(0, 600)}>
                <span className="rail"><span className={`kdot k-${kind(ev, i === events.length - 1, running)}`} /></span>
                <div>
                  <span className="who">{agentName(ev.actor)}{ev.model && ` · ${ev.model}`}</span>
                  <span><b>{label}</b>{detail && <span className="d"> {String(detail).slice(0, 140)}</span>}</span>
                  {ev.reason && ev.action !== "reply" && <i>{ev.reason.slice(0, 200)}</i>}
                </div>
              </motion.li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
