export type State = "planning" | "awaiting_approval" | "approved" | "checking_out" | "completed" | "failed" | "cancelled";

// Audit log entry, streamed over SSE. args/result shapes depend on the action.
export interface AuditEvent {
  ts: string;
  plan_id: string;
  actor: string;
  action: string;
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
  action?: { label: string; send: string };
}

export interface User {
  _id: string;
  name: string;
  email: string;
  household: number;
  diet: string[];
  wallet: number;
  policy: { budget: number };
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
