import { GeoJsonLayer, PolygonLayer, ScatterplotLayer } from "@deck.gl/layers";
import { cellToBoundary } from "h3-js";
import type { BBox } from "../components/MapView";

export interface HexDatum {
  h3: string;
  value: number;
  label?: string;
  selected?: boolean;
}

const boundaryCache = new Map<string, number[][]>();
function boundary(h: string): number[][] {
  let b = boundaryCache.get(h);
  if (!b) {
    b = cellToBoundary(h, true);
    boundaryCache.set(h, b);
  }
  return b;
}

export function hexLayer(
  id: string,
  data: HexDatum[],
  color: (d: HexDatum) => [number, number, number],
  opts: { onClick?: (d: HexDatum) => void; opacity?: number; outline?: [number, number, number] } = {},
) {
  return new PolygonLayer<HexDatum>({
    id,
    data,
    getPolygon: (d) => boundary(d.h3),
    getFillColor: (d) => [...color(d), d.selected ? 240 : 200],
    getLineColor: (d) => (d.selected ? [11, 11, 11, 255] : [...(opts.outline ?? [255, 255, 255]), 90]),
    getLineWidth: (d) => (d.selected ? 2 : 0.5),
    lineWidthUnits: "pixels",
    stroked: true,
    filled: true,
    pickable: true,
    opacity: opts.opacity ?? 0.85,
    onClick: opts.onClick ? (info) => info.object && opts.onClick!(info.object) : undefined,
    updateTriggers: { getFillColor: [data], getLineColor: [data] },
  });
}

export interface PointDatum {
  lon: number;
  lat: number;
  value: number;
  label: string;
}

export function pointLayer(
  id: string,
  data: PointDatum[],
  color: (d: PointDatum) => [number, number, number],
  radius: (d: PointDatum) => number = () => 4,
) {
  return new ScatterplotLayer<PointDatum>({
    id,
    data,
    getPosition: (d) => [d.lon, d.lat],
    getFillColor: (d) => [...color(d), 220],
    getRadius: radius,
    radiusUnits: "pixels",
    radiusMinPixels: 2,
    stroked: true,
    getLineColor: [255, 255, 255, 180],
    lineWidthMinPixels: 1,
    pickable: true,
    updateTriggers: { getFillColor: [data] },
  });
}

export function geometryLayer(id: string, geometry: unknown, color: [number, number, number], width = 3) {
  return new GeoJsonLayer({
    id,
    data: { type: "Feature", geometry, properties: {} } as never,
    stroked: true,
    filled: false,
    getLineColor: [...color, 255],
    getLineWidth: width,
    lineWidthUnits: "pixels",
    getPointRadius: 6,
    pointRadiusUnits: "pixels",
    pickable: false,
  });
}

export function bboxOf(points: { lat: number; lon: number }[]): BBox | null {
  if (!points.length) return null;
  let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
  for (const p of points) {
    a = Math.min(a, p.lon);
    b = Math.min(b, p.lat);
    c = Math.max(c, p.lon);
    d = Math.max(d, p.lat);
  }
  return [a, b, c, d];
}

export function geometryBbox(g: { coordinates: unknown } | null | undefined): BBox | null {
  if (!g) return null;
  const pts: { lat: number; lon: number }[] = [];
  const walk = (x: unknown) => {
    if (Array.isArray(x) && typeof x[0] === "number") pts.push({ lon: x[0] as number, lat: x[1] as number });
    else if (Array.isArray(x)) x.forEach(walk);
  };
  walk(g.coordinates);
  return bboxOf(pts);
}
