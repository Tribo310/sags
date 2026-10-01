export type State = "planning" | "awaiting_approval" | "approved" | "checking_out" | "completed" | "failed" | "cancelled";

// Audit log entry, streamed over SSE. args/result shapes depend on the action.
export interface AuditEvent {
  ts: string;
  plan_id: string;
  actor: string;
  action: string;
  model?: string | null;
  args?: Record<string, any> | null;
  result?: Record<string, any> | null;
  reason?: string | null;
}

export interface Line {
  key: string;
  name: string;
  qty: number;
  price: number;
  line_total: number;
}

export interface Basket {
  stores: Record<string, Line[]>;
  missing: string[];
  total: number;
  savings?: number;
}

export interface Branch {
  store: string;
  name: string;
  location: { coordinates: [number, number] };
  route?: { km?: number; minutes?: number };
}

export interface Trip {
  home: [number, number];  // [lon, lat]
  km: number;
  minutes: number;
  stops: (Branch & { km: number; minutes: number })[];  // visiting order; km/minutes of the leg to the stop
  geometry: [number, number][];  // road line, [lon, lat]
}

export interface Receipt {
  store: string;
  order_id: string;
  total: number;
  pickup_by: string;
  lines: Line[];
}

export interface Plan {
  _id: string;
  state: State;
  version: number;
  basket?: Basket | null;
  budget?: number;
  summary?: string | null;
  branches?: Branch[] | null;
  trip?: Trip | null;
  receipts?: Receipt[];
  error?: string | null;
}

export interface PantryItem {
  key: string;
  name: string;
  unit: string;
  amount: number;
  days_left: number | null;
}

export interface Stats {
  totals: { orders: number; spent: number; saved: number };
  by_store: { store: string; spent: number }[];
  top_items: { name: string; qty: number; spent: number }[];
}

export interface RefillAlert {
  key: string;
  name: string;
  unit: string;
  amount: number;
  days_left: number;
}

export interface PriceAlert {
  store: string;
  key: string;
  name: string;
  old: number;
  new: number;
  pct: number;
  lowest_30d: boolean;
}

export interface ChatItem {
  id: number;
  who: "user" | "agent";
  text: string;
  warn?: boolean;
  actions?: { label: string; send: string }[];
  choice?: boolean;  // the manager's ask: one of actions must be picked
}

export interface User {
  _id: string;
  name: string;
  email: string;
  household: number;
  diet: string[];
  wallet: number;
  location: { type: "Point"; coordinates: [number, number] };
  policy: Policy;
}

export interface Policy {
  budget: number;
  approval_threshold: number;
  max_stores: number;
  allowed_stores: string[];
}

export interface RegisterIn {
  email: string;
  password: string;
  name: string;
  household: number;
  diet: string[];
  budget: number;
  location: [number, number];  // [lon, lat]
}

export type ProfileIn = Omit<RegisterIn, "email" | "password"> & Omit<Policy, "budget">;

export interface PlanRow {
  _id: string;
  state: State;
  created: string;
  total?: number | null;
  title?: string | null;
}

/** A dish the household told the agent it likes or won't eat; rule: a whole food turned diet rule (no_fish). */
export interface Favorite {
  id: string;
  dish: string;
  liked: boolean;
  rule?: string | null;
}
