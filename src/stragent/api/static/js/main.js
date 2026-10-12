/* Startup: wire the form, the radius slider and map clicks; call the API, then render. */
import { analyze, fetchComps, fetchNeighbourhoods, fetchSeasonality, fetchSnapshots } from "./api.js";
import { $, el } from "./dom.js";
import { clearResult, focusComp, onMapClick, showNeighbourhoods, showResult } from "./map.js";
import { renderComps, renderMarket, renderSeason, renderSummary } from "./render.js";

const NO_DATA = "No data loaded yet: run `stragent ingest` first.";

/* An error's message, with the request id when the API sent one (to find it in the server log). */
const describe = (e) => e.message + (e.requestId ? ` (request ${e.requestId})` : "");

function showError(msg) { $("err").textContent = msg; $("err").classList.remove("hidden"); }

async function loadMeta() {
  try {
    const snaps = await fetchSnapshots();
    if (!snaps.length) { showError(NO_DATA); return; }
    for (const s of snaps) $("snapshot").append(el("option", { value: s.snapshot_date, text: s.snapshot_date }));
  } catch (e) { showError(e.code === "no_data" ? NO_DATA : describe(e)); return; }
  try { showNeighbourhoods(await fetchNeighbourhoods()); }
  catch (e) { showError("Could not load the neighbourhood outlines: " + describe(e)); }
}

/* The form as API filters; empty fields are left out. */
function filters() {
  const p = {};
  const num = (id) => $(id).value === "" ? null : Number($(id).value);
  if ($("bedrooms").value !== "") p.bedrooms = Number($("bedrooms").value);
  if ($("snapshot").value) p.snapshot = $("snapshot").value;
  p.min_reviews = num("min_reviews") || 0;
  if (num("min_rating") != null) p.min_rating = num("min_rating");
  if (num("max_min_nights") != null) p.max_min_nights = num("max_min_nights");
  if ($("superhost_only").checked) p.superhost_only = true;
  if (Number($("radius").value) > 0) p.radius_m = Number($("radius").value);
  return p;
}

function render(a, all, season) {
  $("results").classList.remove("hidden");
  const cuts = showResult(a.location, a.filters.radius_m, all.comps);
  renderSummary(a, cuts);
  renderSeason(season);
  renderMarket(a.market);
  renderComps(a, focusComp);
}

onMapClick((lat, lon) => {
  $("address").value = lat.toFixed(5) + ", " + lon.toFixed(5);
  $("f").requestSubmit();
});

$("radius").addEventListener("input", () => {
  const v = Number($("radius").value);
  $("radius_out").textContent = v ? v.toLocaleString("en") + " m" : "Auto";
});
// Re-run when the slider is released, if there is already a result on screen.
$("radius").addEventListener("change", () => {
  if (!$("results").classList.contains("hidden")) $("f").requestSubmit();
});

// A map click or the slider can submit while a search is running: only the newest one may
// touch the page, older ones are dropped when they come back.
let latestRun = 0;

$("f").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const run = ++latestRun;
  $("err").classList.add("hidden");
  $("go").disabled = true; $("go").textContent = "Analysing…";
  try {
    const f = filters();
    const show = Number($("show").value) || 15;
    const analysis = await analyze($("address").value, f, show);
    if (run !== latestRun) return;
    // Same comps again: all of them for the map + seasonality.
    const [comps, season] = await Promise.all([fetchComps(analysis.location, f), fetchSeasonality(analysis.location, f)]);
    if (run !== latestRun) return;
    render(analysis, comps, season);
  } catch (e) {
    if (run !== latestRun) return;
    // Don't leave the previous answer on screen next to the error.
    $("results").classList.add("hidden"); clearResult();
    showError(describe(e));
  } finally {
    if (run === latestRun) { $("go").disabled = false; $("go").textContent = "Analyse"; }
  }
});

loadMeta();
