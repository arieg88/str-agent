/* The bar chart used by the seasonality panel. */
import { css, el } from "./dom.js";

function setAttrs(node, attrs) {
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
}

/* Single-series bar chart, hover tooltip per bar, with a table view behind <details>. */
export function barChart(title, rows, valueFmt, tickEvery = 1) {
  const W = 400, H = 150, padL = 4, padB = 18, padT = 14;
  const n = rows.length, max = Math.max(...rows.map((r) => r.value), 0) || 1;
  const slot = (W - padL) / Math.max(n, 1), bw = Math.max(2, slot - 2);   // 2px gap between bars
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", title);
  const wrap = el("div", { class: "chart" });
  const tip = el("div", { class: "tip hidden" });
  const base = H - padB;
  const line = document.createElementNS(NS, "line");
  setAttrs(line, { x1: 0, x2: W, y1: base, y2: base, stroke: css("--grid") });
  svg.append(line);
  const top = document.createElementNS(NS, "line");   // recessive gridline at the max, labelled
  setAttrs(top, { x1: 0, x2: W, y1: padT, y2: padT, stroke: css("--grid"), "stroke-dasharray": "2 3" });
  const topLabel = document.createElementNS(NS, "text");
  setAttrs(topLabel, { x: W, y: padT - 2, "text-anchor": "end", class: "axis" });
  topLabel.textContent = valueFmt(max);
  svg.append(top, topLabel);
  rows.forEach((r, i) => {
    const h = (r.value / max) * (base - padT), x = padL + i * slot, y = base - h, rad = Math.min(4, bw / 2, h);
    const path = document.createElementNS(NS, "path");
    // bar with 4px rounded top corners, square at the baseline
    path.setAttribute("d", h <= 0 ? `M${x} ${base}h${bw}` :
      `M${x} ${base}V${y + rad}Q${x} ${y} ${x + rad} ${y}H${x + bw - rad}Q${x + bw} ${y} ${x + bw} ${y + rad}V${base}Z`);
    path.setAttribute("fill", css("--series-1"));
    const hit = document.createElementNS(NS, "rect");   // hit target bigger than the mark
    setAttrs(hit, { x: x - 1, y: padT, width: slot, height: base - padT, fill: "transparent" });
    hit.setAttribute("tabindex", "0");
    const show = () => {
      tip.replaceChildren(el("b", { text: valueFmt(r.value) }), " · " + r.label);
      tip.style.left = ((x + bw / 2) / W * 100) + "%"; tip.style.top = (y / H * 100) + "%";
      tip.classList.remove("hidden");
    };
    hit.addEventListener("pointerenter", show); hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", () => tip.classList.add("hidden"));
    hit.addEventListener("blur", () => tip.classList.add("hidden"));
    svg.append(path, hit);
    if (i % tickEvery === 0) {
      const t = document.createElementNS(NS, "text");
      setAttrs(t, { x: x + bw / 2, y: H - 4, "text-anchor": "middle", class: "axis" });
      t.textContent = r.tick; svg.append(t);
    }
  });
  wrap.append(svg, tip);
  const table = el("table", {}, el("tr", {}, el("th", { text: "Month" }), el("th", { text: title })));
  for (const r of rows) table.append(el("tr", {}, el("td", { text: r.label }), el("td", { text: valueFmt(r.value) })));
  const details = el("details", {}, el("summary", { class: "muted", text: "Show as table" }), table);
  return el("div", {}, el("div", { class: "muted", text: title }), wrap, details);
}
