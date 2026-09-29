import type { PantryItem, Plan, Stats } from "./types";

export const USER = "demo";

async function errText(r: Response) {
  try {
    return (await r.json()).detail ?? r.statusText;
  } catch {
    return r.statusText;
  }
}

/** JSON request; throws Error(detail) on a non-2xx answer so callers show the server's reason. */
async function call<T>(url: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const r = await fetch(url, json === undefined ? rest : {
    ...rest, method: rest.method ?? "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(json),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export const api = {
  chat: (text: string, plan_id: string | null) => call<{ plan_id: string }>("/chat", { json: { text, plan_id, user_id: USER } }),
  plan: (id: string) => call<Plan>(`/plans/${id}`),
  approve: (id: string, version: number, confirm_high = false) =>
    call<{ ok?: boolean; needs_second_confirm?: boolean; total?: number }>(`/plans/${id}/approve`, { json: { version, confirm_high } }),
  cancel: (id: string) => call<{ ok: boolean }>(`/plans/${id}/cancel`, { method: "POST" }),
  pantry: () => call<{ pantry: PantryItem[] }>(`/users/${USER}/pantry`),
  stats: () => call<Stats>(`/users/${USER}/stats`),
  tick: () => call<{ updated: number }>(`/users/${USER}/pantry/tick`, { method: "POST" }),
  stt: (audio: Blob) => {
    const fd = new FormData();
    fd.append("audio", audio, "voice.webm");
    return call<{ text: string }>("/stt", { method: "POST", body: fd });
  },
};
