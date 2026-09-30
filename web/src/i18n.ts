import type { AuditEvent, State } from "./types";

export const fmt = (n?: number | null) => Math.round(n ?? 0).toLocaleString("mn-MN") + "₮";

const STORES: Record<string, string> = { nomin: "Номин", emart: "Имарт", sansar: "Сансар" };
export const storeName = (s: string) => STORES[s] ?? s;

const AGENTS: Record<string, string> = {
  manager: "Менежер", meal_planner: "Хоол төлөвлөгч", procurement: "Худалдан авалт", advisor: "Зөвлөх",
  user: "Та", checkout: "Захиалга", wallet: "Хэтэвч", system: "Систем",
};
/** Who did a step: an agent of ours, a store's own agent, or a service. */
export const agentName = (a: string) => AGENTS[a] ?? (STORES[a] ? `${STORES[a]} агент` : a);

export const STATES: Record<State, string> = {
  planning: "Төлөвлөж байна",
  awaiting_approval: "Таны зөвшөөрлийг хүлээж байна",
  approved: "Зөвшөөрсөн",
  checking_out: "Захиалж байна",
  completed: "Захиалга хийгдсэн",
  failed: "Амжилтгүй",
  cancelled: "Цуцалсан",
};
export const stateName = (s?: string) => STATES[s as State] ?? s ?? "";

export const EXAMPLES = [
  "7 хоногийн хүнс, 150 мянган төгрөг, 2 хүн, гахайн мах иддэггүй",
  "Гурван өдрийн хямд хоол, 60 мянгаас хэтрэхгүй",
  "Гэрт 2 кг будаа байгаа. Долоо хоногийн цэс гаргаад өгөөч",
];

/** One readable timeline line per audit event: [label, detail]. */
export function describe(ev: AuditEvent): [string, string] {
  const r = ev.result ?? {}, a = ev.args ?? {};
  const n = (xs?: unknown[]) => xs?.length ?? 0;
  switch (ev.action) {
    case "message": return ["Хүсэлт ирлээ", a.text];
    case "reply": return ["Хариу бичлээ", ""];
    case "ask": return ["Сонголт асуулаа", r.options?.join(" / ") ?? r.error];
    case "state": return ["Төлөв", `${stateName(r.from)} → ${stateName(r.to)}`];
    case "get_profile": return ["Гэр бүлийн мэдээлэл уншлаа", r.household ? `${r.household} хүн, санасан зүйл ${n(r.memory)}` : r.error];
    case "search_recipes": return ["Жор хайлаа", r.recipes ? `${n(r.recipes)} жор олдлоо` : r.error];
    case "search_products": return ["Бараа хайлаа", r.products ? `${n(r.products)} бараа` : r.error];
    case "build_list": return ["Худалдан авах жагсаалт гаргалаа", r.items
      ? `${n(r.items)} бараа, ойролцоогоор ${fmt(r.estimated_total)}` + (n(r.from_pantry) ? `, гэрт байгаа ${n(r.from_pantry)}-г хаслаа` : "")
      : r.error];
    case "get_pantry": return ["Гэрийн нөөцийг шалгалаа", r.pantry ? `${n(r.pantry)} бараа` : r.error];
    case "set_pantry": return ["Гэрийн нөөцийг шинэчиллээ", `${a.key}: ${a.amount}`];
    case "quote_stores": return ["Дэлгүүрүүдээс үнэ асуулаа", r.basket
      ? `${n(r.stores_answered)} дэлгүүр хариулав, хамгийн хямд нь ${fmt(r.basket.total)}` + (r.over_budget ? ", төсвөөс хэтэрлээ" : "")
      : r.error];
    case "plan_meals": return ["Цэс гаргаж, худалдан авах жагсаалт хийлээ", r.recipes
      ? `${r.days} хоног, ${r.items_to_buy} бараа, ойролцоогоор ${fmt(r.estimated_total)}` : r.error];
    case "delegate": return [`${agentName(a.to)}-д даалгавар өглөө`, a.task];
    case "meal_planner": return ["Хоол төлөвлөгч ажлаа хүлээлгэн өглөө", r.recipes
      ? `${r.recipes.join(", ")}; ${r.items_to_buy} бараа авна` : r.error];
    case "procurement": return ["Худалдан авалт ажлаа хүлээлгэн өглөө", r.total != null
      ? `${fmt(r.total)}` + (r.saved_by_negotiation ? `, хэлэлцээрээр ${fmt(r.saved_by_negotiation)} хэмнэв` : "")
      : r.error];
    case "advisor": return ["Зөвлөх хариуллаа", r.answer ?? r.error];
    case "finish": return ["Ажлаа дуусгалаа", r.error ?? r.note ?? ""];
    case "negotiate": return ["Дэлгүүрийн агентуудтай тохиролцлоо", r.basket
      ? `${Object.keys(r.offers ?? {}).length} санал ирэв` + (r.saved_by_negotiation ? `, ${fmt(r.saved_by_negotiation)} хэмнэлээ` : "")
      : r.error];
    case "find_substitute": return ["Орлуулах бараа хайлаа",
      r.substitutes ? `${r.for}: ${r.substitutes.map((x: { name: string }) => x.name).join(", ") || "олдсонгүй"}` : r.error];
    case "make_offer": return ["Санал тавилаа", r.message
      || (n(r.discounts) ? `${n(r.discounts)} бараанд хямдрал` : "хямдрал өгөхгүй")];
    case "reservation_expired": return [`${storeName(ev.actor)}: захиалгын хугацаа дууслаа`, fmt(r.total)];
    case "show_on_map": return ["Газрын зураг дээр харууллаа", r.on_map
      ? r.on_map.map((b: { branch: string; km?: number }) => `${b.branch}${b.km != null ? ` (${b.km} км)` : ""}`).join(", ")
      : r.error];
    case "nearest_branches": return ["Ойрын салбар хайлаа", ""];
    case "get_route": return ["Зам тооцлоо", r.km ? `${r.km} км, ${r.minutes} мин` : ""];
    case "remember": return ["Санаж авлаа", a.fact];
    case "spending_stats": return ["Зарцуулалтаа харлаа", ""];
    case "propose_plan": return ["Төлөвлөгөө санал болголоо", r.total ? fmt(r.total) : r.error];
    case "approve": return ["Та зөвшөөрлөө", fmt(a.total)];
    case "reserve": return [`${storeName(r.store)}: бараа нөөцөллөө`,
      r.result?.ok ? fmt(r.result.total) : `дууссан: ${(r.result?.missing ?? []).join(", ")}`];
    case "reroute": return ["Өөр дэлгүүрээс авна", `${r.name}: ${storeName(r.from_store)} → ${storeName(r.to_store)}, ${fmt(r.price)}`];
    case "hold": return ["Мөнгө түр барьлаа", `${storeName(r.store)}, ${fmt(r.amount)}`];
    case "hold_rejected": return ["Бодлого зөвшөөрсөнгүй", (r.violations ?? []).join("; ")];
    case "capture": return ["Төлбөр хийгдлээ", fmt(r.amount)];
    case "refund": return ["Мөнгө буцаалаа", fmt(r.amount)];
    case "compensate": return ["Бүгдийг буцааж байна", r.reason];
    case "undo_failed": return ["Буцаалт амжилтгүй", r.error];
    case "replan": return ["Дахин төлөвлөж байна", `${r.sold_out} бүх дэлгүүрт дууссан`];
    case "cancel": return ["Та цуцаллаа", ""];
    case "error": return ["Алдаа", r.error];
    default: return [ev.action, r.error ?? ""];
  }
}

export const isFailure = (ev: AuditEvent) =>
  ev.action === "error" || ev.action === "hold_rejected" || (!!ev.result?.error && ev.action !== "reply");

export const DIETS: Record<string, string> = { no_pork: "Гахайн мах иддэггүй", vegetarian: "Цагаан хоолтон" };
