import L from "leaflet";
import { useEffect, useRef } from "react";

import type { Branch, Trip } from "../types";

const UB: [number, number] = [47.918, 106.917];
const BLUE = "#0B5CAD";

/** Leaflet map with the chosen store branches and their drive time; with a trip, the round trip through them. */
export default function MapView({ branches, trip }: { branches?: Branch[] | null; trip?: Trip | null }) {
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
    const pin = ([lon, lat]: [number, number], text: string, color = BLUE) => {
      const popup = document.createElement("div");  // textContent: branch names are data, never HTML
      popup.textContent = text;
      return L.circleMarker([lat, lon], { radius: 10, color, fillOpacity: 0.9 }).bindPopup(popup).addTo(pins);
    };
    let pts: L.LatLng[];
    if (trip) {
      pts = trip.geometry.map(([lon, lat]) => L.latLng(lat, lon));
      L.polyline(pts, { color: BLUE, weight: 4, opacity: 0.7 }).addTo(pins);
      pin(trip.home, "Гэр", "#2E7D32");
      trip.stops.forEach((s, i) => pin(s.location.coordinates, `${i + 1}. ${s.name}, ${s.km} км, ${s.minutes} мин`)
        .bindTooltip(String(i + 1), { permanent: true, direction: "center", className: "stop-no" }));
    } else {
      pts = (branches ?? []).map((br) => {
        pin(br.location.coordinates, `${br.name}, ${br.route?.km ?? "?"} км, ${br.route?.minutes ?? "?"} мин`);
        return L.latLng(br.location.coordinates[1], br.location.coordinates[0]);
      });
    }
    if (pts.length) m.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 14 });
  }, [branches, trip]);

  return (
    <>
      <div className="map" ref={el} role="region" aria-label="Дэлгүүрийн байршил" />
      {trip && (
        <p className="trip">
          Авах зам: гэр → {trip.stops.map((s, i) => `${i + 1}. ${s.name}`).join(" → ")} → гэр, {trip.km} км, {trip.minutes} мин
        </p>
      )}
    </>
  );
}
