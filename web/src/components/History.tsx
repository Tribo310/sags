import { Fragment } from "react";

import { fmt, stateName } from "../i18n";
import type { PlanRow } from "../types";
import { Close } from "./Icons";

interface Props {
  plans: PlanRow[];
  current: string | null;
  onPick: (id: string | null) => void;
  open: boolean;  // drawer state below 1280px; ignored where history is a column
  onClose: () => void;
}

const day = (iso: string) => {
  const d = new Date(iso), today = new Date();
  const diff = Math.round((new Date(today.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 864e5);
  return diff === 0 ? "Өнөөдөр" : diff === 1 ? "Өчигдөр" : d.toLocaleDateString("mn-MN", { month: "long", day: "numeric" });
};

export default function History({ plans, current, onPick, open, onClose }: Props) {
  let last = "";
  return (
    <>
      <div className={`scrim${open ? " open" : ""}`} onClick={onClose} aria-hidden="true" />
      <nav className={`panel history${open ? " open" : ""}`} aria-label="Өмнөх яриа">
        <div className="head">
          <button type="button" className="new" onClick={() => onPick(null)}>+ Шинэ</button>
          <button type="button" className="secondary close" onClick={onClose} aria-label="Хаах"><Close /></button>
        </div>
        <div className="list">
          {plans.length === 0 && <p className="empty">Одоохондоо яриа алга.</p>}
          {plans.map((p) => {
            const d = day(p.created), head = d !== last;
            last = d;
            return (
              <Fragment key={p._id}>
                {head && <h2 className="day">{d}</h2>}
                <button type="button" className="row" aria-current={p._id === current} onClick={() => onPick(p._id)}>
                  <b>{p.title ?? "Шинэ төлөвлөгөө"}</b>
                  <span className="meta st" data-s={p.state}>
                    <span className="dot" />
                    <span className="s">{stateName(p.state)}</span>
                    {!!p.total && <span className="num">{fmt(p.total)}</span>}
                  </span>
                </button>
              </Fragment>
            );
          })}
        </div>
      </nav>
    </>
  );
}
