import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { EXAMPLES } from "../i18n";
import type { ChatItem } from "../types";
import { Arrow, Mic as MicIcon, Stop } from "./Icons";

interface Props {
  items: ChatItem[];
  onSend: (text: string) => void;
  onError: (text: string) => void;
  steps?: React.ReactNode;  // the run's agent steps, shown inside the turn they belong to
}

export default function Chat({ items, onSend, onError, steps }: Props) {
  const [text, setText] = useState("");
  const [used, setUsed] = useState<Set<number>>(new Set());  // action buttons already clicked
  const end = useRef<HTMLDivElement>(null);
  // Block body: newer Chromium returns a Promise from scrollIntoView, which React would call as the cleanup.
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [items.length]);

  const submit = (e: { preventDefault(): void }) => {
    e.preventDefault();
    const t = text.trim();
    if (t) {
      setText("");
      onSend(t);
    }
  };

  // Steps sit before a trailing choice card: the card is what the household has to answer next.
  const split = items.length && items[items.length - 1].choice ? items.length - 1 : items.length;
  const pick = (m: ChatItem, a: string) => {
    setUsed((s) => new Set(s).add(m.id));
    onSend(a);
  };

  return (
    <section className="panel talk" aria-label="Яриа">
      <div className="chat" aria-live="polite">
        {items.length === 0 && (
          <div className="hello">
            <h2>Сайн байна уу?</h2>
            <p>Хэдэн хоног, хэдэн хүн, ямар төсөвтэй вэ? Жишээ:</p>
            <ul>
              {EXAMPLES.map((e, i) => (
                <motion.li key={e} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.06 * i }}>
                  <button type="button" onClick={() => onSend(e)}><span>{e}</span><Arrow /></button>
                </motion.li>
              ))}
            </ul>
          </div>
        )}
        {items.slice(0, split).map((m) => <Bubble key={m.id} m={m} used={used.has(m.id)} onPick={pick} />)}
        {steps}
        {items.slice(split).map((m) => <Bubble key={m.id} m={m} used={used.has(m.id)} onPick={pick} />)}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={submit}>
        <Mic onText={onSend} onError={onError} />
        <input className="field" value={text} onChange={(e) => setText(e.target.value)} placeholder="Юу авах вэ?" autoComplete="off" aria-label="Мессеж" />
        <motion.button whileTap={{ scale: 0.95 }}>Илгээх</motion.button>
      </form>
    </section>
  );
}

function Bubble({ m, used, onPick }: { m: ChatItem; used: boolean; onPick: (m: ChatItem, a: string) => void }) {
  if (m.choice && m.actions?.length) return <Choice m={m} used={used} onPick={onPick} />;
  return (
          <motion.div className={`msg ${m.who}${m.warn ? " warn" : ""}`}
            initial={{ opacity: 0, y: 10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
            style={{ transformOrigin: m.who === "user" ? "bottom right" : "bottom left" }}>
            {m.text}
            {!used && m.actions?.map((a) => (
              <motion.button type="button" key={a.label} className="inline" whileTap={{ scale: 0.97 }}
                onClick={() => onPick(m, a.send)}>{a.label}</motion.button>
            ))}
          </motion.div>
  );
}

/** The manager asks the household to choose: one radio per option, sent with Сонгох. */
function Choice({ m, used, onPick }: { m: ChatItem; used: boolean; onPick: (m: ChatItem, a: string) => void }) {
  const [sel, setSel] = useState(0);
  const opts = m.actions!;
  return (
    <motion.form className={`choice${used ? " used" : ""}`} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      onSubmit={(e) => { e.preventDefault(); onPick(m, opts[sel].send); }}>
      <div>
        <span className="ask">Таны сонголт хэрэгтэй</span>
        <p className="q" id={`q-${m.id}`}>{m.text}</p>
      </div>
      <div className="opts" role="radiogroup" aria-labelledby={`q-${m.id}`}>
        {opts.map((a, i) => (
          <label key={a.label}>
            <input type="radio" name={`choice-${m.id}`} checked={sel === i} disabled={used} onChange={() => setSel(i)} />
            <span>{a.label}</span>
          </label>
        ))}
      </div>
      <button disabled={used}>{used ? "Сонгосон" : "Сонгох"}</button>
    </motion.form>
  );
}

function Mic({ onText, onError }: { onText: (t: string) => void; onError: (t: string) => void }) {
  const rec = useRef<MediaRecorder | null>(null);
  const [recording, setRecording] = useState(false);

  const toggle = async () => {
    if (rec.current?.state === "recording") return rec.current.stop();
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return onError("Микрофон ашиглах зөвшөөрөл алга.");
    }
    const chunks: Blob[] = [];
    const r = new MediaRecorder(stream);
    r.ondataavailable = (e) => chunks.push(e.data);
    r.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      try {
        const { text } = await api.stt(new Blob(chunks, { type: r.mimeType }));
        if (text) onText(text);
      } catch (e) {
        onError(`Дуу танихад алдаа гарлаа: ${(e as Error).message}`);
      }
    };
    rec.current = r;
    r.start();
    setRecording(true);
  };

  return (
    <motion.button type="button" className={`mic${recording ? " rec" : ""}`} onClick={toggle}
      animate={recording ? { scale: [1, 1.08, 1] } : { scale: 1 }}
      transition={recording ? { repeat: Infinity, duration: 1.2 } : undefined}
      aria-label={recording ? "Бичлэг зогсоох" : "Дуугаар хэлэх"}>{recording ? <Stop /> : <MicIcon />}</motion.button>
  );
}
