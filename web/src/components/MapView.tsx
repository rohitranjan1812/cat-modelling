import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre v6 ships its worker as a separate ES module; let Vite bundle it (with its shared
// chunk) into one file in dev and prod, and hand MapLibre the resulting URL.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { MapboxOverlay } from "@deck.gl/mapbox";
import type { Layer, PickingInfo } from "@deck.gl/core";
import type { Mode } from "../lib/palette";

maplibregl.setWorkerUrl(workerUrl);

const STYLES: Record<Mode, string> = {
  light: import.meta.env.VITE_BASEMAP_LIGHT ?? "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: import.meta.env.VITE_BASEMAP_DARK ?? "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};

/** Offline fallback: plain background (data layers still render). */
const BLANK = (mode: Mode): maplibregl.StyleSpecification => ({
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": mode === "dark" ? "#0d0d0d" : "#eceae4" } }],
});

export type BBox = [number, number, number, number];

interface Props {
  layers: Layer[];
  mode: Mode;
  bounds?: BBox | null;
  height?: number | string;
  onMapClick?: (lng: number, lat: number) => void;
  tooltip?: (info: PickingInfo) => string | null;
  cursor?: string;
  children?: React.ReactNode;
}

export function MapView({ layers, mode, bounds, height = 420, onMapClick, tooltip, cursor, children }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const overlay = useRef<MapboxOverlay | null>(null);
  const clickRef = useRef(onMapClick);
  clickRef.current = onMapClick;
  const tipRef = useRef(tooltip);
  tipRef.current = tooltip;
  const lastBounds = useRef<string>("");

  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: STYLES[mode],
      center: [-96, 35],
      zoom: 3.2,
      attributionControl: { compact: true },
    });
    m.on("error", (e: { error?: { message?: string } }) => {
      // Basemap unreachable (offline / blocked): keep data layers on a blank style.
      if (String(e.error?.message ?? "").match(/style|Failed to fetch|NetworkError/i)) m.setStyle(BLANK(mode));
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const o = new MapboxOverlay({
      interleaved: false,
      layers: [],
      getTooltip: (info) => {
        const t = tipRef.current?.(info);
        return t ? { text: t, style: { fontSize: "12px", borderRadius: "6px", padding: "6px 8px" } } : null;
      },
      onClick: (info) => {
        if (!info.layer && info.coordinate && clickRef.current) clickRef.current(info.coordinate[0]!, info.coordinate[1]!);
      },
    });
    m.addControl(o as unknown as maplibregl.IControl);
    map.current = m;
    overlay.current = o;
    return () => {
      m.remove();
      map.current = null;
      overlay.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    map.current?.setStyle(STYLES[mode]);
  }, [mode]);

  useEffect(() => {
    overlay.current?.setProps({ layers });
  }, [layers]);

  useEffect(() => {
    if (!map.current || !bounds || bounds.some((v) => !Number.isFinite(v))) return;
    const key = bounds.map((v) => v.toFixed(2)).join(",");
    if (key === lastBounds.current) return;
    lastBounds.current = key;
    const [a, b, c, d] = bounds;
    const pad = Math.max(0.2, (c - a) * 0.05);
    map.current.fitBounds([[a - pad, b - pad], [c + pad, d + pad]], { padding: 30, duration: 600, maxZoom: 10 });
  }, [bounds]);

  return (
    <div className="map-wrap" style={{ height }}>
      <div ref={el} className="map" style={{ cursor }} />
      {children}
    </div>
  );
}
