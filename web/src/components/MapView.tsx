import L from "leaflet";
import { useEffect, useRef } from "react";

import type { Branch } from "../types";

const UB: [number, number] = [47.918, 106.917];

/** Leaflet map with the chosen store branches and their drive time. */
export default function MapView({ branches }: { branches?: Branch[] | null }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<{ map: L.Map; pins: L.LayerGroup } | null>(null);

  useEffect(() => {
    const m = L.map(el.current!, { scrollWheelZoom: false }).setView(UB, 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap" }).addTo(m);
    map.current = { map: m, pins: L.layerGroup().addTo(m) };
    return () => { m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    if (!map.current) return;
    const { map: m, pins } = map.current;
    pins.clearLayers();
    const pts = (branches ?? []).map((br) => {
      const [lon, lat] = br.location.coordinates;
      const popup = document.createElement("div");  // textContent: branch names are data, never HTML
      popup.textContent = `${br.name}, ${br.route?.km ?? "?"} км, ${br.route?.minutes ?? "?"} мин`;
      L.circleMarker([lat, lon], { radius: 9, color: "#0B5CAD", fillOpacity: 0.9 }).bindPopup(popup).addTo(pins);
      return L.latLng(lat, lon);
    });
    if (pts.length) m.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 14 });
  }, [branches]);

  return <div className="map" ref={el} role="region" aria-label="Дэлгүүрийн байршил" />;
}
