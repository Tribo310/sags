import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { api } from "../api";
import { stateName, storeName } from "../i18n";
import type { Dish, Plan } from "../types";
import Num from "./Num";

interface Props {
  plan: Plan | null;
  running: boolean;
  stores: string[];  // allowed stores, listed while their agents are asked for prices
  onCancel: () => void;
}

const pickup = (iso: string) =>
  new Date(iso).toLocaleString("mn-MN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

const print = { initial: { opacity: 0, y: -4 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0 } };

/** The basket: lines print in, totals count, the budget bar fills. Approve lives in the workspace footer. */
export default function Receipt({ plan, running, stores, onCancel }: Props) {
  const [dish, setDish] = useState<string | null>(null);  // a dish picked: its ingredients light up in the basket
  const b = plan?.basket;
  const budget = plan?.budget;
  const over = !!(b && budget && b.total > budget);
  const paid = !!plan?.receipts?.length;
  const label = plan?.state === "completed" ? "Төлөгдсөн"
    : plan?.state === "cancelled" && paid ? "Буцаасан"
    : plan?.state === "planning" ? "Ноорог" : stateName(plan?.state);

  if (!b) {
    return (
      <section className="section receipt" aria-label="Сагс">
        <h2>Таны сагс</h2>
        <PricesAsOf />
        {plan?.summary && <p className="summary">{plan.summary}</p>}
        {running ? (
          <>
            <p className="lead">Дэлгүүрийн агентууд үнэ харьцуулж байна.</p>
            <div className="pending">
              {stores.map((s) => (
                <div key={s}><span className="kdot k-active" /><b>{storeName(s)}</b><span>Үнэ асууж байна</span></div>
              ))}
            </div>
            <div className="skel" aria-hidden="true"><i /><i /><i /></div>
          </>
        ) : !plan?.summary && (
          <p className="lead">Хүсэлтээ бичээрэй. Агент цэс гаргаж, гурван дэлгүүрийн үнийг харьцуулаад энд тооцоог гаргана.</p>
        )}
        {plan?.error && <p className="note bad">{plan.error}</p>}
      </section>
    );
  }

  return (
    <section className="section receipt" aria-label="Сагс">
      <div className="headrow">
        <h2>Таны сагс</h2>
        <span className="pill st" data-s={plan!.state}>{label}</span>
      </div>
      <PricesAsOf />

      {paid && (
        <motion.div className="orders" {...print}>
          {plan!.receipts!.map((r) => (
            <div key={r.order_id}>
              <b>{storeName(r.store)}</b>
              <span className="id mono">{String(r.order_id).slice(0, 8)}</span>
              <span className="amt"><Num value={r.total} /></span>
              <span className="when">{pickup(r.pickup_by)}-с өмнө авна.</span>
            </div>
          ))}
        </motion.div>
      )}
      {plan?.summary && <motion.p className="summary" {...print}>{plan.summary}</motion.p>}

      {!!plan?.dishes?.length && <Dishes dishes={plan.dishes} picked={dish} onPick={setDish} />}

      <AnimatePresence initial={false}>
        {Object.entries(b.stores).map(([store, lines], si) => (
          <motion.div className="store" key={store} layout {...print}>
            <h3><span>{storeName(store)}</span><Num value={lines.reduce((s, l) => s + l.line_total, 0)} /></h3>
            <AnimatePresence initial={false}>
              {lines.map((l, i) => (
                <motion.div key={l.key} {...print} transition={{ delay: 0.15 * si + 0.03 * i }}
                  className={`line${dish ? (usedBy(plan?.dishes, dish).has(l.key) ? " used" : " dim") : ""}`}>
                  <span>{l.name}</span><span className="num q">×{l.qty}</span><Num value={l.line_total} />
                </motion.div>
              ))}
            </AnimatePresence>
          </motion.div>
        ))}
      </AnimatePresence>

      <motion.div className="sum" layout>
        <div className="row"><span>Нийт</span><span className="big"><Num value={b.total} /></span></div>
        {!!budget && <>
          <div className={`meter${over ? " over" : ""}`}>
            <motion.i initial={{ width: 0 }} animate={{ width: `${Math.min(100, (100 * b.total) / budget)}%` }}
              transition={{ type: "spring", stiffness: 120, damping: 20 }} />
          </div>
          <div className="row budget">
            <span>Төсөв <Num value={budget} /></span>
            <span className={over ? "bad" : "good"}>{over ? "хэтэрсэн " : "үлдэх "}<Num value={Math.abs(budget - b.total)} /></span>
          </div>
        </>}
        {!!b.savings && <p className="note good">Бүгдийг хамгийн үнэтэй дэлгүүрээс авснаас <Num value={b.savings} /> хямд.</p>}
        {!!b.missing?.length && <p className="note bad">Олдоогүй бараа: {b.missing.join(", ")}</p>}
      </motion.div>
      {plan?.error && <p className="note bad">{plan.error}</p>}

      {plan && ["approved", "completed"].includes(plan.state) && (
        <button type="button" className="danger" style={{ alignSelf: "flex-start" }} onClick={onCancel}>
          {plan.state === "completed" ? "Цуцалж мөнгөө буцаах" : "Цуцлах"}
        </button>
      )}
    </section>
  );
}

const UB = (iso: string) => new Date(iso).toLocaleString("mn-MN", {
  timeZone: "Asia/Ulaanbaatar", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});

/** Where the prices come from: the price scout refreshes the stores' real prices once a day on its own. */
function PricesAsOf() {
  const [s, setS] = useState<{ last: string | null; next: string } | null>(null);
  useEffect(() => { api.priceStatus().then(setS, () => {}); }, []);
  if (!s?.last) return null;
  return (
    <p className="asof" title={`Дараагийн шинэчлэл: ${UB(s.next)}`}>
      Номин, Имартын бодит үнэ · {UB(s.last)}-ийн байдлаар · өдөр бүр шинэчлэгдэнэ
    </p>
  );
}

const MEAL: Record<string, string> = { breakfast: "өглөө", lunch: "өдөр", dinner: "орой" };

/** "1, 2, 3-р өдөр орой": the days a dish is eaten, grouped by meal. */
function when(d: Dish) {
  const by: Record<string, number[]> = {};
  for (const w of d.when) (by[w.meal] ??= []).push(w.day);
  return Object.entries(by).map(([meal, days]) => `${days.join(", ")}-р өдөр ${MEAL[meal] ?? meal}`).join(" · ");
}

const usedBy = (dishes: Dish[] | undefined, name: string) =>
  new Set((dishes ?? []).find((d) => d.name === name)?.ingredients.map((g) => g.key) ?? []);

/** What the basket cooks: one card per dish; picking one lights up its ingredients in the store lines below. */
function Dishes({ dishes, picked, onPick }: { dishes: Dish[]; picked: string | null; onPick: (d: string | null) => void }) {
  return (
    <div className="dishes">
      <h3>Энэ сагсаар хийх хоол <span className="hint">· дарж найрлагыг харна</span></h3>
      <ul>
        {dishes.map((d) => (
          <li key={d.name}>
            <button type="button" className={`dish${picked === d.name ? " on" : ""}`} aria-pressed={picked === d.name}
              onClick={() => onPick(picked === d.name ? null : d.name)}>
              <span className="dname">{d.name}<span className="times num">×{d.when.length}</span>
                <span className="dwhen">{when(d)}</span></span>
              {picked === d.name && (
                <span className="ings">
                  {d.ingredients.map((g) => (
                    <span key={g.key} className={`ing ${g.status}`}
                      title={g.status === "home" ? "гэрт байгаа" : g.status === "bought" ? "сагсанд" : "сагсанд ч, гэрт ч алга"}>
                      {g.name}{g.status === "home" && <i> · гэрт</i>}{g.status === "none" && <i> · алга</i>}
                    </span>
                  ))}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
