import { animate, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { fmt } from "../i18n";

/** A number that counts from its previous value to the new one, so a changed total is noticed. */
export default function Num({ value, format = fmt }: { value: number; format?: (n: number) => string }) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(value);
  const current = useRef(value);  // where the count is right now, so an interrupted count continues smoothly

  useEffect(() => {
    if (reduce) {
      current.current = value;
      setShown(value);
      return;
    }
    const c = animate(current.current, value, {
      duration: 0.7, ease: "easeOut",
      onUpdate: (v) => { current.current = v; setShown(v); },
    });
    return () => c.stop();
  }, [value, reduce]);

  return <span className="num">{format(shown)}</span>;
}
