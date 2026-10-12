/* DOM helpers and formatters shared by every module. */

export const $ = (id) => document.getElementById(id);
export const eur = (v) => v == null ? "–" : "€" + Math.round(v).toLocaleString("en");
export const pct = (v) => v == null ? "–" : Math.round(v * 100) + "%";
export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* Build DOM with textContent only: values come from data and are never parsed as HTML. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

export const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
export const monthName = (m) => MONTHS[Number(m.slice(5, 7)) - 1] + " " + m.slice(2, 4);
