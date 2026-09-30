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

  const submit = async (e: { preventDefault(): void }) => {
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
      <form onSubmit={submit}>
        <div className="brandbox"><h1>Сагс</h1><p>хүнсний туслах</p></div>
        <div className="seg">
          {(["login", "register"] as const).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m}
              onClick={() => { setMode(m); setError(""); }}>{m === "login" ? "Нэвтрэх" : "Бүртгүүлэх"}</button>
          ))}
        </div>
        <div className="fields">
          <label className="lbl">И-мэйл<input className="field" type="email" required autoComplete="email" value={f.email} onChange={text("email")} /></label>
          <label className="lbl">Нууц үг<input className="field" type="password" required minLength={reg ? 8 : undefined}
            autoComplete={reg ? "new-password" : "current-password"} value={f.password} onChange={text("password")} /></label>
          {reg && <>
            <label className="lbl">Нэр<input className="field" required maxLength={80} autoComplete="name" value={f.name} onChange={text("name")} /></label>
            <label className="lbl">Гэр бүлийн гишүүд<input className="field mono" type="number" required min={1} max={20} value={f.household} onChange={num("household")} /></label>
            <label className="lbl">Төсөв (₮)
              <span className="money"><input type="number" inputMode="numeric" required min={1000} step={1000} value={f.budget} onChange={num("budget")} /><span>₮</span></span>
            </label>
            <fieldset className="switch-group">
              <legend>Хоолны хязгаарлалт</legend>
              {Object.entries(DIETS).map(([k, label]) => (
                <label className="switch" key={k}>
                  <span>{label}</span>
                  <input type="checkbox" role="switch" checked={f.diet.includes(k)} onChange={() => toggleDiet(k)} />
                </label>
              ))}
            </fieldset>
            <div className="lbl">Гэрийн байршил<PinPicker value={f.location} onChange={(location) => setF((s) => ({ ...s, location }))} /></div>
          </>}
        </div>
        {error && <p className="bad" role="alert">{error}</p>}
        <button className="submit" disabled={busy}>{reg ? "Бүртгүүлэх" : "Нэвтрэх"}</button>
        {!reg && <p className="demo">Туршилтын эрх:<span className="mono">demo@demo.mn / demo1234</span></p>}
      </form>
    </main>
  );
}
