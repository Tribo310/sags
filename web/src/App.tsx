import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import Auth from "./components/Auth";
import Chat from "./components/Chat";
import History from "./components/History";
import MapView from "./components/MapView";
import Pantry from "./components/Pantry";
import Receipt from "./components/Receipt";
import Settings from "./components/Settings";
import Stats from "./components/Stats";
import Timeline from "./components/Timeline";
import { fmt, stateName, storeName } from "./i18n";
import { useEventSource, usePlan } from "./hooks";
import type { AuditEvent, ChatItem, PantryItem, PlanRow, PriceAlert, RefillAlert, Stats as StatsData, User } from "./types";

const EDITABLE = ["planning", "awaiting_approval", "failed"];

function Home({ user, onUser, onLogout }: { user: User; onUser: (u: User) => void; onLogout: () => void }) {
  // The open conversation survives a reload; keyed per account so a shared browser doesn't mix them up.
  const saved = `plan:${user._id}`;
  const [planId, setPlanId] = useState<string | null>(() => {
    try { return localStorage.getItem(saved); } catch { return null; }
  });
  useEffect(() => {
    try { if (planId) localStorage.setItem(saved, planId); else localStorage.removeItem(saved); } catch { /* storage blocked */ }
  }, [planId, saved]);
  const [plans, setPlans] = useState<PlanRow[]>([]);
  const [view, setView] = useState<"chat" | "settings">("chat");
  const [chat, setChat] = useState<ChatItem[]>([]);
  const [pantry, setPantry] = useState<PantryItem[]>([]);
  const [stats, setStats] = useState<StatsData | null>(null);
  const nextId = useRef(0);
  const offered = useRef(new Set<string>());  // refill already offered for these pantry keys

  const say = useCallback((item: Omit<ChatItem, "id">) => setChat((c) => [...c, { ...item, id: nextId.current++ }]), []);
  const warn = useCallback((text: string) => say({ who: "agent", text, warn: true }), [say]);

  const loadHome = useCallback(() => {
    api.pantry().then((r) => setPantry(r.pantry ?? [])).catch(() => {});
    api.stats().then(setStats).catch(() => {});
  }, []);
  useEffect(loadHome, [loadHome]);
  const loadPlans = useCallback(() => { api.plans().then(setPlans).catch(() => {}); }, []);
  useEffect(loadPlans, [loadPlans]);

  // Chat bubbles come from the plan's audit log, so a reconnect replays the same conversation.
  const onEvent = (ev: AuditEvent) => {
    const r = ev.result ?? {};
    if (ev.actor === "user" && ev.action === "message") say({ who: "user", text: ev.args?.text });
    if (ev.action === "reply" && r.text) say({ who: "agent", text: r.text });
    if (ev.action === "ask" && r.text) say({ who: "agent", text: r.text, actions: r.options?.map((o: string) => ({ label: o, send: o })) });
    if (ev.action === "reroute")
      say({ who: "agent", text: `${r.name} ${storeName(r.from_store)}-д дууссан тул ${storeName(r.to_store)}-оос ${fmt(r.price)}-өөр авна.` });
    if (ev.action === "replan") warn(`${r.sold_out} бүх дэлгүүрт дууссан. Төлөвлөгөөг шинэчилж байна.`);
    if (ev.action === "state" && ["completed", "cancelled"].includes(r.to)) loadHome();
    if (ev.action === "state") loadPlans();
  };
  const { plan, events, push, reset } = usePlan(planId);

  // Pantry change stream: offer a refill once per item when it is about to run out.
  const onRefill = (a: RefillAlert) => {
    loadHome();
    if (offered.current.has(a.key)) return;
    offered.current.add(a.key);
    say({
      who: "agent", text: `${a.name} ${a.days_left} хоногийн дараа дуусна, ${a.amount} ${a.unit} үлдсэн.`,
      actions: [{ label: `${a.name} захиалах`, send: `${a.name} дуусах гэж байна. 7 хоногийн хэрэгцээгээр нь захиалъя.` }],
    });
  };

  // Price change stream: a store cut a price by 10% or more.
  const onPrice = (a: PriceAlert) => {
    say({
      who: "agent",
      text: `${a.name} ${storeName(a.store)}-д ${a.pct}% хямдарлаа: ${fmt(a.old)} → ${fmt(a.new)}.${a.lowest_30d ? " Сүүлийн 30 хоногийн хамгийн бага үнэ." : ""}`,
      actions: [{ label: "Дахин тооцоолох", send: `${a.name} ${storeName(a.store)}-д хямдарсан. Сагсаа дахин үнэлж, хэмнэлт гарвал шинэчил.` }],
    });
  };

  // One live connection per tab for all three: browsers allow only 6 per host, and three streams each hung the
  // page after a refresh or with a second tab open.
  useEventSource<(AuditEvent | RefillAlert | PriceAlert) & { stream: "event" | "alert" | "price" }>(
    `/me/stream${planId ? `?plan_id=${planId}` : ""}`,
    (m) => {
      if (m.stream === "event") { push(m as AuditEvent); onEvent(m as AuditEvent); }
      else if (m.stream === "alert") onRefill(m as RefillAlert);
      else onPrice(m as PriceAlert);
    },
    () => { reset(); setChat([]); },  // the server replays the plan's events on every (re)connect
  );

  const send = async (text: string) => {
    try {
      const reuse = plan && EDITABLE.includes(plan.state) ? planId : null;
      const { plan_id } = await api.chat(text, reuse);
      setPlanId(plan_id);
      loadPlans();  // the first message is stored before /chat answers, so the new row already has its title
    } catch (e) {
      warn(`${text}\n${(e as Error).message}`);
    }
  };

  const approve = async (confirmHigh = false) => {
    if (!plan) return;
    try {
      const r = await api.approve(plan._id, plan.version, confirmHigh);
      if (r.needs_second_confirm && confirm(`${fmt(r.total)} нь таны баталгаажуулах босгоос их байна. Үргэлжлүүлэх үү?`)) approve(true);
    } catch (e) {
      warn((e as Error).message);
    }
  };

  const pick = (id: string | null) => {
    setView("chat");
    if (id === planId) return;
    setChat([]);
    setPlanId(id);
  };

  const cancel = () => plan && api.cancel(plan._id).catch((e) => warn(e.message));
  const tick = () => api.tick().then(loadHome).catch((e) => warn(e.message));

  return (
    <MotionConfig reducedMotion="user">
      <header className="top">
        <h1>Сагс</h1>
        <p>хүнсний туслах</p>
        <motion.span className="state" data-s={plan?.state} layout aria-live="polite">
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={plan?.state ?? "new"} style={{ display: "inline-block" }}
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}>
              {plan ? stateName(plan.state) : "Шинэ"}
            </motion.span>
          </AnimatePresence>
        </motion.span>
        <span className="who">{user.name}</span>
        <button type="button" className="quiet" aria-pressed={view === "settings"}
          onClick={() => setView(view === "chat" ? "settings" : "chat")}>Тохиргоо</button>
        <button type="button" className="quiet" onClick={onLogout}>Гарах</button>
      </header>
      {view === "settings" ? (
        <main className="auth"><Settings user={user} onSaved={onUser} onClose={() => setView("chat")} /></main>
      ) : (
      <main>
        <History plans={plans} current={planId} onPick={pick} />
        <div className="col">
          <Chat items={chat} onSend={send} onError={warn} />
          <Timeline events={events} />
        </div>
        <div className="col right">
          <Receipt plan={plan} onApprove={() => approve()} onCancel={cancel} />
          <MapView branches={plan?.branches} trip={plan?.trip} />
          <div className="home">
            <Pantry items={pantry} onTick={tick} />
            <Stats stats={stats} />
          </div>
        </div>
      </main>
      )}
    </MotionConfig>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);  // undefined: still asking /auth/me
  const loadUser = useCallback(() => api.me().then(setUser, () => setUser(null)), []);
  useEffect(() => { loadUser(); }, [loadUser]);
  useEffect(() => {
    const out = () => setUser(null);
    window.addEventListener("logout", out);
    return () => window.removeEventListener("logout", out);
  }, []);

  if (user === undefined) return null;
  if (!user) return <Auth onDone={loadUser} />;
  // key: a different account starts with fresh chat/plan state
  return <Home key={user._id} user={user} onUser={setUser} onLogout={() => api.logout().catch(() => {}).then(() => setUser(null))} />;
}
