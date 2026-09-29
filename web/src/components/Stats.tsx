import { motion } from "motion/react";

import { storeName } from "../i18n";
import type { Stats as StatsData } from "../types";
import Num from "./Num";

export default function Stats({ stats }: { stats: StatsData | null }) {
  const t = stats?.totals;
  if (!stats || !t?.orders) {
    return (
      <section className="card">
        <h3>Зарцуулалт</h3>
        <p className="empty">Эхний захиалгын дараа энд зарцуулалт, хэмнэлт харагдана.</p>
      </section>
    );
  }
  const max = Math.max(...stats.by_store.map((x) => x.spent));
  return (
    <section className="card">
      <h3>Зарцуулалт</h3>
      <p><b><Num value={t.spent} /></b>, {t.orders} захиалга, <span className="good"><Num value={t.saved} /> хэмнэлт</span></p>
      <div className="bars">
        {stats.by_store.map((x, i) => (
          <div key={x.store}>
            {storeName(x.store)} <Num value={x.spent} />
            <motion.i initial={{ width: 0 }} animate={{ width: `${(100 * x.spent) / max}%` }}
              transition={{ type: "spring", stiffness: 120, damping: 20, delay: 0.08 * i }} />
          </div>
        ))}
      </div>
      {stats.top_items.length > 0 && (
        <table className="top-items"><tbody>
          {stats.top_items.map((x) => <tr key={x.name}><td>{x.name}</td><td className="n"><Num value={x.spent} /></td></tr>)}
        </tbody></table>
      )}
    </section>
  );
}
