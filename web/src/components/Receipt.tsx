import { AnimatePresence, motion } from "motion/react";

import { storeName } from "../i18n";
import type { Plan } from "../types";
import Num from "./Num";

interface Props {
  plan: Plan | null;
  onApprove: () => void;
  onCancel: () => void;
}

const pickup = (iso: string) =>
  new Date(iso).toLocaleString("mn-MN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

const print = { initial: { opacity: 0, y: -4 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0 } };

/** The basket as a paper receipt: lines print in, totals count, the budget bar fills, a stamp lands once paid. */
export default function Receipt({ plan, onApprove, onCancel }: Props) {
  const b = plan?.basket;
  const budget = plan?.budget;
  const over = !!(b && budget && b.total > budget);
  const stamp = plan?.state === "completed" ? "ТӨЛӨГДСӨН" : plan?.state === "cancelled" && plan.receipts?.length ? "БУЦААСАН" : null;

  return (
    <section className="paper" aria-label="Сагс">
      <h2>Таны сагс</h2>
      {!plan?.summary && !b && (
        <p className="empty">Хүсэлтээ бичээрэй. Агент цэс гаргаж, гурван дэлгүүрийн үнийг харьцуулаад энд тооцоог гаргана.</p>
      )}
      {plan?.summary && <motion.p className="summary" {...print}>{plan.summary}</motion.p>}

      <AnimatePresence initial={false}>
        {b && Object.entries(b.stores).map(([store, lines], si) => (
          <motion.div className="store" key={store} layout {...print}>
            <h3><span>{storeName(store)}</span><Num value={lines.reduce((s, l) => s + l.line_total, 0)} /></h3>
            <table><tbody>
              <AnimatePresence initial={false}>
                {lines.map((l, i) => (
                  <motion.tr key={l.key} {...print} transition={{ delay: 0.15 * si + 0.03 * i }}>
                    <td>{l.name}</td><td className="n">×{l.qty}</td><td className="n"><Num value={l.line_total} /></td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody></table>
          </motion.div>
        ))}
      </AnimatePresence>

      {b && (
        <motion.div className="sum" layout>
          <div className="row"><span>Нийт</span><span className="big"><Num value={b.total} /></span></div>
          {!!budget && <>
            <div className={`meter${over ? " over" : ""}`}>
              <motion.i initial={{ width: 0 }} animate={{ width: `${Math.min(100, (100 * b.total) / budget)}%` }}
                transition={{ type: "spring", stiffness: 120, damping: 20 }} />
            </div>
            <div className="row">
              <span>Төсөв <Num value={budget} /></span>
              <span className={over ? "bad" : "good"}>{over ? "хэтэрсэн " : "үлдэх "}<Num value={Math.abs(budget - b.total)} /></span>
            </div>
          </>}
        </motion.div>
      )}
      {!!b?.savings && <motion.p className="note good" {...print}>Бүгдийг хамгийн үнэтэй дэлгүүрээс авснаас <Num value={b.savings} /> хямд.</motion.p>}
      {!!b?.missing?.length && <p className="note bad">Олдоогүй бараа: {b.missing.join(", ")}</p>}

      {plan?.receipts?.map((r, i) => (
        <motion.p className="note" key={r.order_id} {...print} transition={{ delay: 0.1 * i }}>
          {storeName(r.store)}, захиалга {String(r.order_id).slice(0, 8)}: <Num value={r.total} />. {pickup(r.pickup_by)}-с өмнө авна.
        </motion.p>
      ))}
      {plan?.error && <motion.p className="note bad" {...print}>{plan.error}</motion.p>}

      <AnimatePresence>
        {stamp && (
          <motion.div className="stamp" key={stamp}
            initial={{ scale: 2.2, opacity: 0, rotate: -12 }} animate={{ scale: 1, opacity: 0.85, rotate: -12 }} exit={{ opacity: 0 }}
            transition={{ type: "spring", stiffness: 500, damping: 18 }}>{stamp}</motion.div>
        )}
      </AnimatePresence>

      <div className="actions">
        <AnimatePresence>
          {plan?.state === "awaiting_approval" && b && (
            <motion.button className="approve" onClick={onApprove} key="approve"
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} whileTap={{ scale: 0.97 }}>
              <Num value={b.total} /> төлөхийг зөвшөөрөх
            </motion.button>
          )}
        </AnimatePresence>
        {plan && ["awaiting_approval", "approved", "completed"].includes(plan.state) && (
          <motion.button className="quiet" onClick={onCancel} whileTap={{ scale: 0.97 }}>
            {plan.state === "completed" ? "Цуцалж мөнгөө буцаах" : "Цуцлах"}
          </motion.button>
        )}
      </div>
    </section>
  );
}
