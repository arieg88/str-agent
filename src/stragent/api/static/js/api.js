/* Calls to the stragent API. Errors carry the API's "detail" message. */

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

/* "40.63, 22.94" is sent as coordinates, anything else as an address to geocode. */
export function locationOf(text) {
  const m = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
  return m ? { lat: Number(m[1]), lon: Number(m[2]) } : { address: text.trim() };
}

function query(location, filters) {
  const qs = new URLSearchParams({ lat: location.latitude, lon: location.longitude });
  for (const [k, v] of Object.entries(filters)) qs.set(k, v);
  return qs;
}

export const fetchSnapshots = () => api("/api/snapshots");
export const fetchNeighbourhoods = () => api("/api/neighbourhoods.geojson");

export function analyze(text, filters, show) {
  return api("/api/analyze", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...locationOf(text), ...filters, show }),
  });
}

/* By coordinates (no second geocode): location is the one returned by analyze(). */
export const fetchComps = (location, filters) => api("/api/comps?" + query(location, filters));
export const fetchSeasonality = (location, filters) => api("/api/seasonality?" + query(location, filters));
