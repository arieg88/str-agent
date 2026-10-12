/* The side panels: summary, seasonality, market and comps. */
import { barChart } from "./charts.js";
import { $, css, el, eur, monthName, pct } from "./dom.js";

function kpi(label, range, fmt) {
  return el("div", { class: "kpi" },
    el("div", { class: "v", text: range ? fmt(range.median) : "–" }),
    el("div", { class: "l", text: label }),
    el("div", { class: "r", text: range ? `${fmt(range.p25)} – ${fmt(range.p75)}` : "" }));
}

/* cuts: the price quartile cuts used to colour the comp markers, for the legend. */
export function renderSummary(a, cuts) {
  const box = $("summary"); box.replaceChildren();
  const f = a.filters, loc = a.location;
  box.append(el("h2", { text: loc.neighbourhood || "Selected location" }));
  box.append(el("div", { class: "muted",
    text: `${a.comp_stats.count} comps · ${f.room_type} · bedrooms ${f.bedrooms ?? "any"}${f.bedrooms != null ? " ±" + f.bedroom_tolerance : ""} · within ${f.radius_m} m${f.radius_mode === "auto" ? " (auto)" : ""} · snapshot ${a.snapshot_date}` }));
  for (const w of a.warnings) box.append(el("div", { class: "warn", text: w }));
  const grid = el("div", { class: "kpis", style: "margin-top:10px" },
    kpi("Nightly price", a.comp_stats.nightly_price, eur),
    kpi("Revenue / year", a.revenue.annual_revenue, eur),
    kpi("Occupancy", a.revenue.occupancy, pct),
    kpi("Nights / year", a.revenue.occupied_nights, (v) => Math.round(v)));
  box.append(grid);
  box.append(el("div", { class: "muted", style: "margin-top:8px",
    text: "Median with 25th–75th percentile across comps with recent reviews. Occupancy is inferred from reviews (review rate " +
      pct(a.revenue.assumptions.review_rate) + ", cap " + pct(a.revenue.assumptions.max_occupancy) + ")." }));
  const legend = el("div", { class: "legend" }, el("span", {}, el("i", { style: "background:" + css("--accent") }), "Your flat"));
  const labels = [`≤ ${eur(cuts[0])}`, `≤ ${eur(cuts[1])}`, `≤ ${eur(cuts[2])}`, `> ${eur(cuts[2])}`];
  ["--q1", "--q2", "--q3", "--q4"].forEach((v, i) => {
    legend.append(el("span", {}, el("i", { style: "background:" + css(v) }), labels[i]));
  });
  box.append(el("div", { class: "muted", style: "margin-top:8px", text: "Map: comps by nightly price" }), legend);
}

export function renderSeason(s) {
  const box = $("season"); box.replaceChildren(el("h2", { text: "Seasonality" }));
  if (!s.listing_count) { box.append(el("div", { class: "muted", text: "No comps to chart." })); return; }
  const cal = s.forward_calendar.map((m) => ({ value: m.unavailable_share, label: monthName(m.month), tick: monthName(m.month).slice(0, 1) }));
  const rev = s.review_history.map((m) => ({ value: m.reviews_per_listing, label: monthName(m.month), tick: monthName(m.month).slice(0, 1) }));
  box.append(barChart("Reviews per comp, by month (past demand)", rev, (v) => v.toFixed(2)));
  box.append(el("div", { style: "height:10px" }));
  box.append(barChart("Nights not open, next 12 months (booked or blocked)", cal, pct));
  for (const n of s.notes) box.append(el("div", { class: "muted", style: "margin-top:6px", text: n }));
}

export function renderMarket(m) {
  const box = $("market");
  const dl = el("dl", {});
  const rows = [
    ["Listings (all types)", m.total_listings], ["Entire homes", pct(m.entire_home_share)],
    ["Run by multi-listing hosts", pct(m.multi_listing_host_share)], ["Held by top 3 hosts", pct(m.top3_hosts_share)],
    ["Show a licence number", pct(m.licensed_share)], ["First reviewed in last 12 months", pct(m.new_listings_share)],
  ];
  for (const [k, v] of rows) dl.append(el("dt", { text: k }), el("dd", { text: String(v) }));
  box.replaceChildren(el("h2", { text: `Market within ${m.radius_m} m` }), dl);
}

/* onSelect(id) is called when a row is clicked. */
export function renderComps(a, onSelect) {
  const box = $("comps");
  const table = el("table", {}, el("tr", {},
    ...["Dist.", "Bd", "Price", "Rev. 12m", "Rev. total", "Rating", "Est. rev.", "Since"].map((h) => el("th", { text: h }))));
  for (const c of a.comps) {
    const tr = el("tr", { class: "comp", title: c.name || "" },
      el("td", { text: Math.round(c.distance_m) + " m" }), el("td", { text: c.bedrooms ?? "–" }),
      el("td", { text: eur(c.price) }), el("td", { text: c.number_of_reviews_ltm ?? "–" }),
      el("td", { text: c.number_of_reviews ?? "–" }),
      el("td", { text: c.review_scores_rating != null ? c.review_scores_rating.toFixed(2) : "–" }),
      el("td", { text: eur(c.est_revenue) }), el("td", { text: c.active_since ? c.active_since.slice(0, 7) : "–" }));
    tr.addEventListener("click", () => onSelect(c.id));
    table.append(tr);
  }
  box.replaceChildren(el("h2", { text: `Nearest comps (${a.comps.length} of ${a.comp_stats.count})` }), table,
    el("div", { class: "muted", style: "margin-top:6px", text: "Click a row to find it on the map. “Since” = first review." }));
}
