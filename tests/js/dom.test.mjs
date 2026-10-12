import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./load.mjs";

const { el, httpsUrl } = await load("dom.js");

test("httpsUrl keeps absolute https links", () => {
  assert.equal(httpsUrl("https://www.airbnb.com/rooms/10000148"), "https://www.airbnb.com/rooms/10000148");
});

test("httpsUrl drops everything else", () => {
  const bad = [
    "javascript:alert(1)", " javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\nscript:alert(1)",
    "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)", "http://www.airbnb.com/rooms/1",
    "//evil.example/rooms/1", "/rooms/1", "rooms/1", "", null, undefined, 42, {},
  ];
  for (const value of bad) assert.equal(httpsUrl(value), null, String(value));
});

/* Just enough of a document for el() to set attributes on. */
function fakeDocument() {
  return { createElement: (tag) => ({ tag, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } }) };
}

test("el leaves off an href that is not https", () => {
  globalThis.document = fakeDocument();
  try {
    const bad = el("a", { href: "javascript:alert(1)", rel: "noopener", text: "Open on Airbnb" });
    assert.deepEqual(bad.attrs, { rel: "noopener" });
    assert.equal(bad.textContent, "Open on Airbnb");
    const good = el("a", { href: "https://www.airbnb.com/rooms/1" });
    assert.deepEqual(good.attrs, { href: "https://www.airbnb.com/rooms/1" });
  } finally { delete globalThis.document; }
});
