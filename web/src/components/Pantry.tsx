import { motion } from "motion/react";

import type { PantryItem } from "../types";
import Num from "./Num";

const LOW_DAYS = 5;  // shown amber from here; the planner's refill alert fires later, at 2
const FULL_DAYS = 14;  // a full bar
const amount = (n: number) => String(Math.round(n * 100) / 100);

/** What the household has, soonest to run out first; items with no consumption estimate listed apart, without a bar. */
export default function Pantry({ items, onTick }: { items: PantryItem[]; onTick: () => void }) {
  const timed = items.filter((i) => i.days_left !== null).sort((a, b) => a.days_left! - b.days_left!);
  const rest = items.filter((i) => i.days_left === null);
  return (
    <>
      <div className="headrow">
        <h2>Гэрт байгаа</h2>
        <motion.button type="button" className="secondary" onClick={onTick} whileTap={{ scale: 0.95 }}>Нэг өдөр өнгөрөөх</motion.button>
      </div>
      {items.length === 0 && <p className="empty">Гэрт юу байгааг агентад хэлбэл энд харагдана.</p>}
      {timed.length > 0 && (
        <div className="plist">
          {timed.map((i) => {
            const d = i.days_left!, low = d < LOW_DAYS;
            return (
              <div key={i.key} className={`prow${low ? " low" : ""}`}>
                <span>{i.name}</span>
                <span className="num"><Num value={i.amount} format={amount} /> {i.unit}</span>
                <span className="num days"><Num value={d} format={amount} /> хоног</span>
                <span className="bar">
                  <motion.i initial={false} animate={{ width: `${Math.min(100, (d / FULL_DAYS) * 100)}%` }}
                    transition={{ type: "spring", stiffness: 140, damping: 22 }} />
                </span>
              </div>
            );
          })}
        </div>
      )}
      {rest.length > 0 && <>
        <h3 className="sub">Хэрэглээний тооцоогүй</h3>
        <div className="plist">
          {rest.map((i) => (
            <div key={i.key} className="prow rest">
              <span>{i.name}</span><span className="num"><Num value={i.amount} format={amount} /> {i.unit}</span>
            </div>
          ))}
        </div>
      </>}
    </>
  );
}
