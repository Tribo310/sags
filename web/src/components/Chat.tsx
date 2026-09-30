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
            {m.who === "agent" && !m.warn && m.text && <Speak text={m.text} onError={onError} />}
            {m.action && !used.has(m.id) && (
              <motion.button type="button" className="inline" whileTap={{ scale: 0.97 }} onClick={() => {
                setUsed((s) => new Set(s).add(m.id));
                onSend(m.action!.send);
              }}>{m.action.label}</motion.button>
            )}
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


/** tsuurAI takes at most 800 characters: split at sentence ends, then at spaces if a sentence is still too long. */
export function chunks(text: string, max = 700): string[] {
  const out: string[] = [];
  let cur = "";
  for (const s of text.match(/[^.!?\n]+[.!?\n]*/g) ?? [text]) {
    if ((cur + s).length <= max) { cur += s; continue; }
    if (cur.trim()) out.push(cur.trim());
    cur = s;
    while (cur.length > max) {
      const cut = cur.lastIndexOf(" ", max) > 0 ? cur.lastIndexOf(" ", max) : max;
      out.push(cur.slice(0, cut).trim());
      cur = cur.slice(cut);
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

let playing: { stop: () => void } | null = null;  // one reply read aloud at a time

/** Reads an agent reply aloud (tsuurAI), chunk by chunk; pressing again stops. */
function Speak({ text, onError }: { text: string; onError: (t: string) => void }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const me = useRef<{ stop: () => void } | null>(null);

  const play = async () => {
    if (state !== "idle") return me.current?.stop();
    playing?.stop();
    let stopped = false, audio: HTMLAudioElement | null = null;
    const handle = { stop: () => { stopped = true; audio?.pause(); setState("idle"); } };
    me.current = playing = handle;
    setState("loading");
    try {
      const parts = chunks(text);
      let next = api.tts(parts[0]);
      for (let i = 0; i < parts.length && !stopped; i++) {
        const wav = await next;
        if (i + 1 < parts.length) next = api.tts(parts[i + 1]);  // fetch the next part while this one plays
        if (stopped) break;
        const url = URL.createObjectURL(wav);
        audio = new Audio(url);
        setState("playing");
        await new Promise<void>((done) => { audio!.onended = () => done(); audio!.onpause = () => done(); audio!.play().catch(() => done()); });
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      if (!stopped) onError(`Уншуулж чадсангүй: ${(e as Error).message}`);
    } finally {
      if (playing === handle) playing = null;
      setState("idle");
    }
  };

  const label = state === "idle" ? "Сонсох" : "Зогсоох";
  return (
    <button type="button" className={`speak ${state}`} onClick={play} aria-label={label} title={label}>
      {state === "idle" ? "🔊" : state === "loading" ? "…" : "⏹"}
    </button>
  );
}
