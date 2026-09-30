import { useState } from "react";

import { api } from "../api";
import { DIETS } from "../i18n";
import type { RegisterIn } from "../types";
import PinPicker, { UB } from "./PinPicker";

export default function Auth({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [f, setF] = useState<RegisterIn>({ email: "", password: "", name: "", household: 2, diet: [], budget: 150000, location: UB });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const reg = mode === "register";

  const text = (k: "email" | "password" | "name") => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const num = (k: "household" | "budget") => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: Number(e.target.value) });
  const toggleDiet = (k: string) => setF({ ...f, diet: f.diet.includes(k) ? f.diet.filter((d) => d !== k) : [...f.diet, k] });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await (reg ? api.register(f) : api.login(f.email, f.password));
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Сагс</h1>
        <div className="tabs">
          {(["login", "register"] as const).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m} className={mode === m ? "" : "quiet"}
              onClick={() => { setMode(m); setError(""); }}>{m === "login" ? "Нэвтрэх" : "Бүртгүүлэх"}</button>
          ))}
        </div>
        <label>И-мэйл<input type="email" required autoComplete="email" value={f.email} onChange={text("email")} /></label>
        <label>Нууц үг<input type="password" required minLength={reg ? 8 : undefined}
          autoComplete={reg ? "new-password" : "current-password"} value={f.password} onChange={text("password")} /></label>
        {reg && <>
          <label>Нэр<input required maxLength={80} autoComplete="name" value={f.name} onChange={text("name")} /></label>
          <label>Гэр бүлийн гишүүд<input type="number" required min={1} max={20} value={f.household} onChange={num("household")} /></label>
          <label>Төсөв (₮)<input type="number" required min={1000} step={1000} value={f.budget} onChange={num("budget")} /></label>
          <fieldset>
            <legend>Хоолны хязгаарлалт</legend>
            {Object.entries(DIETS).map(([k, label]) => (
              <label className="check" key={k}>
                <input type="checkbox" checked={f.diet.includes(k)} onChange={() => toggleDiet(k)} />{label}
              </label>
            ))}
          </fieldset>
          <div className="label">Гэрийн байршил<PinPicker value={f.location} onChange={(location) => setF((s) => ({ ...s, location }))} /></div>
        </>}
        {error && <p className="bad" role="alert">{error}</p>}
        <button disabled={busy}>{reg ? "Бүртгүүлэх" : "Нэвтрэх"}</button>
        {!reg && <p className="empty">Туршилтын эрх: demo@demo.mn / demo1234</p>}
      </form>
    </main>
  );
}
