import { useEffect, useState } from "react";

import { api } from "../api";
import { DIETS, storeName } from "../i18n";
import type { Favorite, ProfileIn, User } from "../types";
import { Check } from "./Icons";
import PinPicker from "./PinPicker";

const STORES = ["nomin", "emart", "sansar"];

interface Props {
  user: User;
  onSaved: (u: User) => void;
  onClose: () => void;
  onLogout: () => void;
}

export default function Settings({ user, onSaved, onClose, onLogout }: Props) {
  const { budget, approval_threshold, max_stores, allowed_stores } = user.policy;
  const [f, setF] = useState<ProfileIn>({
    name: user.name, household: user.household, diet: user.diet, location: user.location.coordinates,
    budget, approval_threshold, max_stores, allowed_stores,
  });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const num = (k: "household" | "budget" | "approval_threshold") =>
    (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: Number(e.target.value) });
  const toggle = (k: "diet" | "allowed_stores", v: string) =>
    setF({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v] });
  const people = (d: number) => setF({ ...f, household: Math.min(20, Math.max(1, f.household + d)) });

  const submit = async (e: { preventDefault(): void }) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      onSaved(await api.updateMe(f));
      setMsg({ ok: true, text: "Хадгалагдлаа. Дараагийн төлөвлөгөөнд үйлчилнэ." });
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const [lon, lat] = f.location;
  return (
    <div className="settings">
      <form onSubmit={submit}>
        <button type="button" className="quiet back" onClick={onClose}>← Чат руу буцах</button>
        <h1>Тохиргоо</h1>
        <div className="cards">
          <fieldset className="card">
            <legend>Гэр бүл</legend>
            <label className="lbl">Нэр
              <input className="field" required maxLength={80} autoComplete="name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
            </label>
            <div className="lbl"><span id="hh">Гэр бүлийн гишүүд</span>
              <div className="stepper-in">
                <button type="button" aria-label="Хасах" onClick={() => people(-1)} disabled={f.household <= 1}>−</button>
                <input type="number" aria-labelledby="hh" required min={1} max={20} value={f.household} onChange={num("household")} />
                <button type="button" aria-label="Нэмэх" onClick={() => people(1)} disabled={f.household >= 20}>+</button>
              </div>
            </div>
          </fieldset>

          <fieldset className="card">
            <legend>Мөнгө</legend>
            <label className="lbl">Төсөв (₮)
              <span className="money"><input type="number" inputMode="numeric" required min={1000} step={1000} value={f.budget} onChange={num("budget")} /><span>₮</span></span>
            </label>
            <label className="lbl">Баталгаажуулах босго (₮)
              <span className="money"><input type="number" inputMode="numeric" required min={0} max={f.budget} step={1000}
                value={f.approval_threshold} onChange={num("approval_threshold")} /><span>₮</span></span>
            </label>
          </fieldset>

          <fieldset className="card">
            <legend>Дэлгүүр</legend>
            <div className="lbl"><span id="ms">Хэдэн дэлгүүрээс авах вэ</span>
              <div className="seg" role="radiogroup" aria-labelledby="ms">
                {[1, 2, 3].map((n) => (
                  <label key={n}>
                    <input type="radio" name="max_stores" checked={f.max_stores === n} onChange={() => setF({ ...f, max_stores: n })} />{n}
                  </label>
                ))}
              </div>
            </div>
            <div className="lbl"><span id="as">Зөвшөөрсөн дэлгүүрүүд</span>
              <div className="chips" role="group" aria-labelledby="as">
                {STORES.map((s) => (
                  <label className="chip" key={s}>
                    <input type="checkbox" checked={f.allowed_stores.includes(s)} onChange={() => toggle("allowed_stores", s)}
                      disabled={f.allowed_stores.length === 1 && f.allowed_stores.includes(s)} />
                    <span className="box"><Check size={11} /></span>{storeName(s)}
                  </label>
                ))}
              </div>
            </div>
          </fieldset>

          <fieldset className="card" style={{ gap: 4 }}>
            <legend style={{ marginBottom: 8 }}>Хоолны хязгаарлалт</legend>
            {Object.entries(DIETS).map(([k, label]) => (
              <label className="switch" key={k}>
                <span>{label}</span>
                <input type="checkbox" role="switch" checked={f.diet.includes(k)} onChange={() => toggle("diet", k)} />
              </label>
            ))}
          </fieldset>

          <Favorites />

          <div className="card wide">
            <div className="ctitle"><span>Гэрийн байршил</span><span className="num">{lat.toFixed(4)}, {lon.toFixed(4)}</span></div>
            <PinPicker value={f.location} onChange={(location) => setF((s) => ({ ...s, location }))} />
          </div>
        </div>
        <div className="save">
          <button disabled={busy}>Хадгалах</button>
          {msg && <p className={msg.ok ? "good" : "bad"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
          <button type="button" className="danger" onClick={onLogout}>Гарах</button>
        </div>
      </form>
    </div>
  );
}

/** Dishes saved from the chat ("бид цуйванд дуртай"); removing one here takes it out of future plans. */
function Favorites() {
  const [items, setItems] = useState<Favorite[] | null>(null);
  useEffect(() => { api.favorites().then(setItems, () => setItems([])); }, []);
  const forget = async (id: string) => {
    await api.forgetFavorite(id).catch(() => {});
    setItems((xs) => (xs ?? []).filter((x) => x.id !== id));
  };
  return (
    <fieldset className="card favorites">
      <legend>Чатаас хадгалсан хоол</legend>
      {items === null ? <span className="empty">…</span>
        : items.length === 0 ? <span className="empty">Чатад "цуйванд дуртай", "загас иддэггүй" гэвэл энд нэмэгдэнэ.</span>
        : items.map((f) => (
          <span key={f.id} className={`fav ${f.liked ? "liked" : "no"}`}>
            {f.liked ? "❤" : "✕"} {f.dish}{f.rule ? " (бүх хоолноос)" : ""}
            <button type="button" className="quiet" aria-label={`${f.dish} устгах`} onClick={() => forget(f.id)}>×</button>
          </span>
        ))}
    </fieldset>
  );
}
