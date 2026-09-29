import { motion } from "motion/react";

import type { PantryItem } from "../types";
import Num from "./Num";

const LOW_DAYS = 2;  // same threshold as the planner's refill alert
const amount = (n: number) => String(Math.round(n * 100) / 100);

export default function Pantry({ items, onTick }: { items: PantryItem[]; onTick: () => void }) {
  return (
    <section className="card">
      <h3>Гэрт байгаа <motion.button className="quiet" onClick={onTick} whileTap={{ scale: 0.95 }}>Нэг өдөр өнгөрөөх</motion.button></h3>
      {items.length === 0 && <p className="empty">Гэрт юу байгааг агентад хэлбэл энд харагдана.</p>}
      <table><tbody>
        {items.map((i) => {
          const d = i.days_left, low = d !== null && d <= LOW_DAYS;
          return (
            <tr key={i.key}>
              <td>
                {i.name}
                <div className={`days${low ? " low" : ""}`}>
                  <div>
                    <motion.i initial={false} animate={{ width: `${d === null ? 100 : Math.min(100, (d / 7) * 100)}%` }}
                      transition={{ type: "spring", stiffness: 140, damping: 22 }} />
                  </div>
                  {d !== null && <motion.span key={String(low)} initial={low ? { scale: 1.3 } : false} animate={{ scale: 1 }}>
                    <Num value={d} format={amount} /> хоног
                  </motion.span>}
                </div>
              </td>
              <td className="n"><Num value={i.amount} format={amount} /> {i.unit}</td>
            </tr>
          );
        })}
      </tbody></table>
    </section>
  );
}
