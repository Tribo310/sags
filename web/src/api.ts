import type { PantryItem, Plan, RegisterIn, Stats, User } from "./types";

const NO_RETRY = ["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"];

async function errText(r: Response) {
  try {
    const d = (await r.json()).detail;
    return Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d ?? r.statusText;  // 422 detail is a list
  } catch {
    return r.statusText;
  }
}

/** App.tsx listens for this and shows the login screen. */
export const loggedOut = () => window.dispatchEvent(new Event("logout"));

let refreshing: Promise<boolean> | null = null;
/** New access token from the refresh cookie; requests that hit 401 together share one call. */
export function refresh() {
  refreshing ??= fetch("/auth/refresh", { method: "POST" }).then((r) => r.ok, () => false).finally(() => { refreshing = null; });
  return refreshing;
}

/** JSON request; on 401 refreshes once and retries; throws Error(detail) on a non-2xx answer. */
async function call<T>(url: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const send = () => fetch(url, json === undefined ? rest : {
    ...rest, method: rest.method ?? "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(json),
  });
  let r = await send();
  if (r.status === 401 && !NO_RETRY.includes(url)) {
    if (await refresh()) r = await send();
    if (r.status === 401) loggedOut();
  }
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export const api = {
  me: () => call<User>("/auth/me"),
  login: (email: string, password: string) => call<{ ok: boolean }>("/auth/login", { json: { email, password } }),
  register: (body: RegisterIn) => call<{ ok: boolean }>("/auth/register", { json: body }),
  logout: () => call<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  chat: (text: string, plan_id: string | null) => call<{ plan_id: string }>("/chat", { json: { text, plan_id } }),
  plan: (id: string) => call<Plan>(`/plans/${id}`),
  approve: (id: string, version: number, confirm_high = false) =>
    call<{ ok?: boolean; needs_second_confirm?: boolean; total?: number }>(`/plans/${id}/approve`, { json: { version, confirm_high } }),
  cancel: (id: string) => call<{ ok: boolean }>(`/plans/${id}/cancel`, { method: "POST" }),
  pantry: () => call<{ pantry: PantryItem[] }>("/me/pantry"),
  stats: () => call<Stats>("/me/stats"),
  tick: () => call<{ updated: number }>("/me/pantry/tick", { method: "POST" }),
  stt: (audio: Blob) => {
    const fd = new FormData();
    fd.append("audio", audio, "voice.webm");
    return call<{ text: string }>("/stt", { method: "POST", body: fd });
  },
};
