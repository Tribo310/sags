import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { EXAMPLES } from "../i18n";
import type { ChatItem } from "../types";

interface Props {
  items: ChatItem[];
  onSend: (text: string) => void;
  onError: (text: string) => void;
}

export default function Chat({ items, onSend, onError }: Props) {
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

  return (
    <section className="talk" aria-label="Яриа">
      <div className="chat" aria-live="polite">
        {items.length === 0 && (
          <div className="hello">
            <h2>Сайн байна уу?</h2>
            <p>Хэдэн хоног, хэдэн хүн, ямар төсөвтэй вэ? Жишээ:</p>
            {EXAMPLES.map((e, i) => (
              <motion.button type="button" key={e} onClick={() => onSend(e)}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * i }}
                whileHover={{ x: 4 }} whileTap={{ scale: 0.98 }}>{e}</motion.button>
            ))}
          </div>
        )}
        {items.map((m) => (
          <motion.div key={m.id} className={`msg ${m.who}${m.warn ? " warn" : ""}`}
            initial={{ opacity: 0, y: 10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
            style={{ transformOrigin: m.who === "user" ? "bottom right" : "bottom left" }}>
            {m.text}
            {!used.has(m.id) && m.actions?.map((a) => (
              <motion.button type="button" key={a.label} className="inline" whileTap={{ scale: 0.97 }} onClick={() => {
                setUsed((s) => new Set(s).add(m.id));
                onSend(a.send);
              }}>{a.label}</motion.button>
            ))}
          </motion.div>
        ))}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={submit}>
        <Mic onText={onSend} onError={onError} />
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Юу авах вэ?" autoComplete="off" aria-label="Мессеж" />
        <motion.button whileTap={{ scale: 0.95 }}>Илгээх</motion.button>
      </form>
    </section>
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
      aria-label={recording ? "Бичлэг зогсоох" : "Дуугаар хэлэх"}>{recording ? "⏹" : "🎤"}</motion.button>
  );
}
