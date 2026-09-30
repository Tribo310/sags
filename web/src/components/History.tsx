import { fmt, stateName } from "../i18n";
import type { PlanRow } from "../types";

interface Props {
  plans: PlanRow[];
  current: string | null;
  onPick: (id: string | null) => void;
}

export default function History({ plans, current, onPick }: Props) {
  return (
    <nav className="history" aria-label="Өмнөх яриа">
      <button type="button" onClick={() => onPick(null)}>+ Шинэ</button>
      {plans.length === 0 && <p className="empty">Одоохондоо яриа алга.</p>}
      {plans.map((p) => (
        <button type="button" key={p._id} className="row" aria-current={p._id === current} onClick={() => onPick(p._id)}>
          <b>{p.title ?? "Шинэ төлөвлөгөө"}</b>
          <span>{new Date(p.created).toLocaleDateString("mn-MN")} · {stateName(p.state)}{p.total ? ` · ${fmt(p.total)}` : ""}</span>
        </button>
      ))}
    </nav>
  );
}
