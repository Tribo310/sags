import { motion } from "motion/react";

import { storeName } from "../i18n";
import type { Stats as StatsData } from "../types";
import Num from "./Num";

export default function Stats({ stats }: { stats: StatsData | null }) {
  const t = stats?.totals;
  if (!stats || !t?.orders) {
    return (
      <div className="spend">
        <h2>Зарцуулалт</h2>
        <p className="empty">Эхний захиалгын дараа энд зарцуулалт, хэмнэлт харагдана.</p>
      </div>
    );
  }
  const max = Math.max(...stats.by_store.map((x) => x.spent));
  return (
    <div className="spend">
      <h2>Зарцуулалт</h2>
      <p className="total">
        <span className="big"><Num value={t.spent} /></span>
        <span>{t.orders} захиалга · <span className="good"><Num value={t.saved} /> хэмнэлт</span></span>
      </p>
      {stats.by_store.map((x, i) => (
        <div key={x.store} className="sbar">
          <div><span>{storeName(x.store)}</span><Num value={x.spent} /></div>
          <span>
            <motion.i initial={{ width: 0 }} animate={{ width: `${(100 * x.spent) / max}%` }}
              transition={{ type: "spring", stiffness: 120, damping: 20, delay: 0.08 * i }} />
          </span>
        </div>
      ))}
      {stats.top_items.length > 0 && <>
        <h3 className="sub">Хамгийн их зарцуулсан</h3>
        <div>
          {stats.top_items.map((x) => <div key={x.name} className="trow"><span>{x.name}</span><Num value={x.spent} /></div>)}
        </div>
      </>}
    </div>
  );
}
