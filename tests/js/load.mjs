/* Import a map page module from source. The modules are plain browser files with no package.json
   around them, so they are loaded as data: URLs (always ES modules, on any Node version).
   Only works for modules without imports of their own (dom.js, api.js). */
import { readFile } from "node:fs/promises";

const STATIC_JS = new URL("../../src/stragent/api/static/js/", import.meta.url);

export async function load(name) {
  const source = await readFile(new URL(name, STATIC_JS), "utf8");
  return import("data:text/javascript," + encodeURIComponent(source));
}
