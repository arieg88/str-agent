/* The Leaflet map: neighbourhood layer, pin, radius circle and comp markers. */
import { css, el, eur } from "./dom.js";

const L = globalThis.L;   // loaded from the CDN by index.html, the page's only global

const map = L.map("map", { zoomControl: true }).setView([40.6326, 22.9416], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: "&copy; OpenStreetMap contributors · listings: Inside Airbnb",
}).addTo(map);
const layers = { hoods: null, pin: null, circle: null, comps: L.layerGroup().addTo(map) };
const markersById = new Map();

export function onMapClick(handler) {
  map.on("click", (e) => handler(e.latlng.lat, e.latlng.lng));
}

export function showNeighbourhoods(geo) {
  layers.hoods = L.geoJSON(geo, {
    style: { color: css("--text-muted"), weight: 1, fillOpacity: 0.03 },
    onEachFeature: (f, layer) => layer.bindTooltip(f.properties.name, { sticky: true }),
  }).addTo(map);
  if (geo.features.length) map.fitBounds(layers.hoods.getBounds(), { padding: [10, 10] });
}

function quartiles(values) {
  const s = [...values].sort((a, b) => a - b);
  const q = (p) => s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))];
  return [q(0.25), q(0.5), q(0.75)];
}

function popup(c) {
  return el("div", {},
    el("b", { text: eur(c.price) + " / night" }), el("br"),
    `${c.bedrooms ?? "?"} bd · ${c.number_of_reviews_ltm ?? 0} reviews in 12 m (${c.number_of_reviews ?? 0} total) · rating ${c.review_scores_rating ?? "–"}`, el("br"),
    `Est. revenue ${eur(c.est_revenue)} · active since ${c.active_since ? c.active_since.slice(0, 7) : "–"}`, el("br"),
    el("a", { href: c.url, target: "_blank", rel: "noopener", text: "Open on Airbnb" }));
}

/* Draw the pin, the radius circle and the comps; returns the price quartile cuts for the legend. */
export function showResult(loc, radiusM, comps) {
  if (layers.pin) map.removeLayer(layers.pin);
  if (layers.circle) map.removeLayer(layers.circle);
  layers.circle = L.circle([loc.latitude, loc.longitude], {
    radius: radiusM, color: css("--text-secondary"), weight: 1, dashArray: "4 4", fill: false,
  }).addTo(map);
  layers.pin = L.circleMarker([loc.latitude, loc.longitude], {
    radius: 9, color: css("--surface-1"), weight: 3, fillColor: css("--accent"), fillOpacity: 1,
  }).addTo(map).bindTooltip("Your flat", { direction: "top" });
  map.fitBounds(layers.circle.getBounds(), { padding: [20, 20] });

  // comps coloured by price quartile (ordinal blue ramp), 2px surface ring
  layers.comps.clearLayers(); markersById.clear();
  const prices = comps.map((c) => c.price);
  const cuts = prices.length ? quartiles(prices) : [0, 0, 0];
  const qColor = (p) => css(p <= cuts[0] ? "--q1" : p <= cuts[1] ? "--q2" : p <= cuts[2] ? "--q3" : "--q4");
  for (const c of comps) {
    const m = L.circleMarker([c.latitude, c.longitude], {
      radius: 6, color: css("--surface-1"), weight: 2, fillColor: qColor(c.price), fillOpacity: 1,
    });
    m.bindPopup(popup(c));
    m.addTo(layers.comps); markersById.set(c.id, m);
  }
  return cuts;
}

/* Centre on a comp and open its popup. */
export function focusComp(id) {
  const m = markersById.get(id);
  if (m) { map.setView(m.getLatLng(), Math.max(map.getZoom(), 16)); m.openPopup(); }
}
