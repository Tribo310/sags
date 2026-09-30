import L from "leaflet";
import { useEffect, useRef } from "react";

import { fmt, storeName } from "../i18n";
import type { Branch, Receipt, Trip } from "../types";
import { Home } from "./Icons";

const BLUE = "#0B5CAD";

// Markers carry only a number (never data), so the HTML is ours.
const pinIcon = (cls: string, text: string, size: number) =>
  L.divIcon({ className: "", html: `<span class="pin ${cls}" style="width:${size}px;height:${size}px">${text}</span>`, iconSize: [size, size] });

interface Props {
  branches?: Branch[] | null;
  trip?: Trip | null;
  home: [number, number];  // [lon, lat]
  receipts?: Receipt[];
}

/** The pickup tab: before checkout the home pin and chosen branches; after it, the ordered trip with numbered stops. */
export default function MapView({ branches, trip, home, receipts }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<{ map: L.Map; pins: L.LayerGroup } | null>(null);

  useEffect(() => {
    const m = L.map(el.current!, { scrollWheelZoom: false }).setView([home[1], home[0]], 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap" }).addTo(m);
    map.current = { map: m, pins: L.layerGroup().addTo(m) };
    return () => { m.remove(); map.current = null; };
  }, []);  // home is only the starting view

  useEffect(() => {
    if (!map.current) return;
    const { map: m, pins } = map.current;
    pins.clearLayers();
    const pin = ([lon, lat]: [number, number], text: string, icon: L.DivIcon) => {
      const popup = document.createElement("div");  // textContent: branch names are data, never HTML
      popup.textContent = text;
      return L.marker([lat, lon], { icon, title: text }).bindPopup(popup).addTo(pins);
    };
    const at = ([lon, lat]: [number, number]) => L.latLng(lat, lon);
    const from = trip?.home ?? home;
    pin(from, "Гэр", pinIcon("home", "", 20));
    let pts = [at(from)];
    if (trip) {
      L.polyline(trip.geometry.map(at), { color: BLUE, weight: 4, opacity: 0.75 }).addTo(pins);
      trip.stops.forEach((s, i) => pin(s.location.coordinates, `${i + 1}. ${s.name}, ${s.km} км, ${s.minutes} мин`, pinIcon("stop", String(i + 1), 26)));
      pts = pts.concat(trip.geometry.map(at));
    } else {
      (branches ?? []).forEach((br) => {
        pin(br.location.coordinates, `${br.name}, ${br.route?.km ?? "?"} км, ${br.route?.minutes ?? "?"} мин`, pinIcon("stop", "", 18));
        pts.push(at(br.location.coordinates));
      });
    }
    if (pts.length > 1) m.fitBounds(L.latLngBounds(pts), { padding: [32, 32], maxZoom: 15 });
    else m.setView(pts[0], 13);
  }, [branches, trip, home]);

  const order = (store: string) => receipts?.find((r) => r.store === store);
  return (
    <section className="section route" aria-label="Авах зам">
      <div className="map" ref={el} role="region" aria-label="Дэлгүүрийн байршил" />
      {trip ? (
        <>
          <div className="headrow"><h2>Авах зам</h2><span className="num">{trip.km} км · {trip.minutes} мин</span></div>
          <ol className="stops">
            <li className="home"><span><Home size={14} /></span><div><b>Гэр</b><small>Эхлэл</small></div><span /></li>
            {trip.stops.map((s, i) => {
              const r = order(s.store);
              return (
                <li key={s.name}>
                  <span>{i + 1}</span>
                  <div><b>{s.name}</b><small>{storeName(s.store)}{r && ` · ${String(r.order_id).slice(0, 8)}`} · {s.km} км, {s.minutes} мин</small></div>
                  <span className="num">{r ? fmt(r.total) : ""}</span>
                </li>
              );
            })}
            <li className="home"><span><Home size={14} /></span><div><b>Гэр</b><small>Төгсгөл</small></div><span /></li>
          </ol>
        </>
      ) : (
        <p className="muted">Захиалга хийгдсэний дараа дэлгүүрүүдээр дамжих авах замыг энд гаргана.</p>
      )}
    </section>
  );
}
