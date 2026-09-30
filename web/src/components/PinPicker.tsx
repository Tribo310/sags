import L from "leaflet";
import { useEffect, useRef } from "react";

export const UB: [number, number] = [106.917, 47.918];  // [lon, lat], Sukhbaatar square

/** Click the map to move the home pin. value/onChange are [lon, lat] like GeoJSON. */
export default function PinPicker({ value, onChange }: { value: [number, number]; onChange: (v: [number, number]) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const cb = useRef(onChange);
  cb.current = onChange;
  useEffect(() => {
    const m = L.map(el.current!, { scrollWheelZoom: false }).setView([value[1], value[0]], 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap" }).addTo(m);
    const pin = L.circleMarker([value[1], value[0]], { radius: 9, color: "#0B5CAD", fillOpacity: 0.9 }).addTo(m);
    m.on("click", (e) => {
      pin.setLatLng(e.latlng);
      cb.current([e.latlng.lng, e.latlng.lat]);
    });
    return () => { m.remove(); };
  }, []);  // value is only the starting point
  return <div className="map" ref={el} role="region" aria-label="Гэрийн байршил: газрын зураг дээр дарж сонгоно уу" />;
}
