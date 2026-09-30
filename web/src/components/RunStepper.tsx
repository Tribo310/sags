import { stateName } from "../i18n";
import type { AuditEvent, Plan } from "../types";
import { Check } from "./Icons";

const PHASES = ["Цэс", "Үнэ", "Сонголт", "Зөвшөөрөл", "Төлбөр", "Авах"];
const MENU_DONE = ["meal_planner", "plan_meals", "build_list"];

/** Where the run is, 0..5 (-1: no plan yet). Planning is split by what the agents have produced so far. */
export function phaseOf(plan: Plan | null, events: AuditEvent[]): number {
  if (!plan) return -1;
  if (plan.state === "awaiting_approval") return 3;
  if (plan.state === "approved" || plan.state === "checking_out") return 4;
  if (plan.state === "completed" || plan.receipts?.length) return 5;
  if (plan.basket) return 2;
  return events.some((e) => MENU_DONE.includes(e.action) && !e.result?.error) ? 1 : 0;
}

/** Desktop: six named steps, the current one is the state pill. Phone: pill, six segments and n/6. */
export default function RunStepper({ plan, phase }: { plan: Plan | null; phase: number }) {
  const s = plan?.state ?? "new";
  const label = plan ? stateName(plan.state) : "Шинэ";
  return (
    <>
      <ol className="stepper st" data-s={s} aria-label="Явцын алхам">
        {PHASES.map((p, i) => {
          const done = i < phase, cur = i === phase;
          return (
            <li key={p} className={done ? "done" : cur ? "cur" : ""} aria-current={cur ? "step" : undefined}>
              {cur ? (
                <span className="pill" aria-live="polite"><span className="n">{i + 1}</span>{label}</span>
              ) : (
                <><span className="n">{done ? <Check size={11} /> : i + 1}</span><span className="label">{p}</span></>
              )}
              {cur && <span className="sr-only">{p}</span>}
            </li>
          );
        })}
      </ol>
      <div className="runstrip st" data-s={s}>
        <span className="pill">{label}</span>
        <div className="segs" aria-hidden="true">
          {PHASES.map((p, i) => <i key={p} className={i < phase ? "done" : i === phase ? "cur" : ""} />)}
        </div>
        <span className="of">{Math.max(phase + 1, 0)}/6</span>
      </div>
    </>
  );
}
