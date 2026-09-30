import { useState } from "react";

import { api } from "../api";
import { DIETS, storeName } from "../i18n";
import type { ProfileIn, User } from "../types";
import PinPicker from "./PinPicker";

const STORES = ["nomin", "emart", "sansar"];

export default function Settings({ user, onSaved, onClose }: { user: User; onSaved: (u: User) => void; onClose: () => void }) {
  const { budget, approval_threshold, max_stores, allowed_stores } = user.policy;
  const [f, setF] = useState<ProfileIn>({
    name: user.name, household: user.household, diet: user.diet, location: user.location.coordinates,
    budget, approval_threshold, max_stores, allowed_stores,
  });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const num = (k: "household" | "budget" | "approval_threshold" | "max_stores") =>
    (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: Number(e.target.value) });
  const toggle = (k: "diet" | "allowed_stores", v: string) =>
    setF({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v] });

  const submit = async (e: React.FormEvent) => {
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

  return (
    <form className="card auth-card" onSubmit={submit}>
      <h1>Тохиргоо</h1>
      <label>Нэр<input required maxLength={80} autoComplete="name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label>Гэр бүлийн гишүүд<input type="number" required min={1} max={20} value={f.household} onChange={num("household")} /></label>
      <label>Төсөв (₮)<input type="number" required min={1000} step={1000} value={f.budget} onChange={num("budget")} /></label>
      <label>Баталгаажуулах босго (₮)<input type="number" required min={0} max={f.budget} step={1000}
        value={f.approval_threshold} onChange={num("approval_threshold")} /></label>
      <label>Хэдэн дэлгүүрээс авах вэ<input type="number" required min={1} max={3} value={f.max_stores} onChange={num("max_stores")} /></label>
      <fieldset>
        <legend>Зөвшөөрсөн дэлгүүрүүд</legend>
        {STORES.map((s) => (
          <label className="check" key={s}>
            <input type="checkbox" checked={f.allowed_stores.includes(s)} onChange={() => toggle("allowed_stores", s)}
              disabled={f.allowed_stores.length === 1 && f.allowed_stores.includes(s)} />{storeName(s)}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Хоолны хязгаарлалт</legend>
        {Object.entries(DIETS).map(([k, label]) => (
          <label className="check" key={k}>
            <input type="checkbox" checked={f.diet.includes(k)} onChange={() => toggle("diet", k)} />{label}
          </label>
        ))}
      </fieldset>
      <div className="label">Гэрийн байршил<PinPicker value={f.location} onChange={(location) => setF((s) => ({ ...s, location }))} /></div>
      {msg && <p className={msg.ok ? "good" : "bad"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      <button disabled={busy}>Хадгалах</button>
      <button type="button" className="quiet" onClick={onClose}>Буцах</button>
    </form>
  );
}
