import { MotionConfig } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import Auth from "./components/Auth";
import Chat from "./components/Chat";
import History from "./components/History";
import { Basket, Chat as ChatIcon, Home as HomeIcon, Route } from "./components/Icons";
import MapView from "./components/MapView";
import Pantry from "./components/Pantry";
import Receipt from "./components/Receipt";
import RunStepper, { phaseOf } from "./components/RunStepper";
import Settings from "./components/Settings";
import Stats from "./components/Stats";
import Timeline from "./components/Timeline";
import { fmt, storeName } from "./i18n";
import { useEventSource, usePlan } from "./hooks";
import type { AuditEvent, ChatItem, PantryItem, PlanRow, PriceAlert, RefillAlert, Stats as StatsData, User } from "./types";

const EDITABLE = ["planning", "awaiting_approval", "failed"];
const SETTLED = ["awaiting_approval", "completed", "failed", "cancelled"];
type Tab = "chat" | "basket" | "route" | "pantry";
const WORK_TABS: [Tab, string][] = [["basket", "Сагс"], ["route", "Зам"], ["pantry", "Гэрт байгаа"]];
const PHONE_TABS: [Tab, string, React.ComponentType][] = [
  ["chat", "Чат", ChatIcon], ["basket", "Сагс", Basket], ["route", "Зам", Route], ["pantry", "Гэр", HomeIcon],
];

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
  const [tab, setTab] = useState<Tab>("chat");  // phone: the one visible pane; desktop: chat means the basket tab
  const [drawer, setDrawer] = useState(false);  // history drawer below 1280px
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
    if (ev.action === "ask" && r.text)
      say({ who: "agent", text: r.text, choice: !!r.options?.length, actions: r.options?.map((o: string) => ({ label: o, send: o })) });
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
    setDrawer(false);
    if (id === planId) return;
    setChat([]);
    setPlanId(id);
  };

  const cancel = () => plan && api.cancel(plan._id).catch((e) => warn(e.message));
  const tick = () => api.tick().then(loadHome).catch((e) => warn(e.message));

  useEffect(() => {
    if (!drawer) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [drawer]);

  const last = events[events.length - 1];
  // A run is live until the manager's last word or a settled state. Not plan.running: the manager's loop returns
  // (and clears it) while the agents it delegated to are still working.
  const running = !!plan && !!last && !SETTLED.includes(plan.state) && !["reply", "ask", "error"].includes(last.action);
  const phase = phaseOf(plan, events);
  const work: Tab = tab === "chat" ? "basket" : tab;
  const awaiting = plan?.state === "awaiting_approval";

  return (
    <MotionConfig reducedMotion="user">
      <div className="app">
        <header className="top">
          <div className="brand"><h1>Сагс</h1><p>хүнсний туслах</p></div>
          <RunStepper plan={plan} phase={phase} />
          <nav aria-label="Бүртгэл">
            <button type="button" className="secondary hist-btn" aria-expanded={drawer}
              onClick={() => { setView("chat"); setDrawer(true); }}>Түүх</button>
            <span className="who">{user.name}</span>
            <button type="button" className="quiet d-only" aria-pressed={view === "settings"}
              onClick={() => setView(view === "chat" ? "settings" : "chat")}>Тохиргоо</button>
            <button type="button" className="secondary m-only" aria-pressed={view === "settings"}
              onClick={() => setView(view === "chat" ? "settings" : "chat")}>Тохиргоо</button>
            <button type="button" className="quiet d-only" onClick={onLogout}>Гарах</button>
          </nav>
        </header>

        {view === "settings" ? (
          <Settings user={user} onSaved={onUser} onClose={() => setView("chat")} onLogout={onLogout} />
        ) : (
          <>
            <main className="board" data-tab={tab}>
              <History plans={plans} current={planId} onPick={pick} open={drawer} onClose={() => setDrawer(false)} />
              <Chat items={chat} onSend={send} onError={warn} steps={<Timeline events={events} running={running} />} />
              <section className="panel work" aria-label="Ажлын талбар">
                <div className="tabs" role="tablist">
                  {WORK_TABS.map(([k, label]) => (
                    <button key={k} type="button" role="tab" aria-selected={work === k} onClick={() => setTab(k)}>
                      {label}{k === "basket" && awaiting && <span className="badge" aria-label="зөвшөөрөл хүлээж байна" />}
                    </button>
                  ))}
                </div>
                <div className="body" role="tabpanel">
                  {work === "basket" && <Receipt plan={plan} running={running} stores={user.policy.allowed_stores} onCancel={cancel} />}
                  {work === "route" && <MapView branches={plan?.branches} trip={plan?.trip} home={user.location.coordinates} receipts={plan?.receipts} />}
                  {work === "pantry" && (
                    <section className="section pantry" aria-label="Гэрт байгаа">
                      <Pantry items={pantry} onTick={tick} />
                      <Stats stats={stats} />
                    </section>
                  )}
                </div>
                {awaiting && plan?.basket && work === "basket" && (
                  <div className="approve-bar">
                    <button type="button" className="approve" onClick={() => approve()}>{fmt(plan.basket.total)} төлөхийг зөвшөөрөх</button>
                    <button type="button" className="secondary" onClick={cancel}>Цуцлах</button>
                  </div>
                )}
              </section>
            </main>
            <nav className="tabbar" role="tablist" aria-label="Хэсэг">
              {PHONE_TABS.map(([k, label, Icon]) => (
                <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
                  <Icon />
                  <span>{label}{k === "basket" && awaiting && <span className="badge" aria-label="зөвшөөрөл хүлээж байна" />}</span>
                </button>
              ))}
            </nav>
          </>
        )}
      </div>
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
  if (!user) return <div className="app"><Auth onDone={loadUser} /></div>;
  // key: a different account starts with fresh chat/plan state
  return <Home key={user._id} user={user} onUser={setUser} onLogout={() => api.logout().catch(() => {}).then(() => setUser(null))} />;
}
