import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { load } from "./load.mjs";

const { ApiError, analyze, fetchComps, fetchSnapshots, locationOf } = await load("api.js");

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

function respond(status, body, headers = {}) {
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status, headers });
}

test("an API error keeps the whole error shape", async () => {
  respond(422, { error: "out_of_area", detail: "Outside Thessaloniki", request_id: "abc123" });
  await assert.rejects(analyze("37.98, 23.72", {}, 15), (e) => {
    assert.ok(e instanceof ApiError);
    assert.deepEqual([e.message, e.code, e.requestId], ["Outside Thessaloniki", "out_of_area", "abc123"]);
    return true;
  });
});

test("an error without a JSON body falls back to the status and the request id header", async () => {
  globalThis.fetch = async () =>
    new Response("oops", { status: 502, statusText: "Bad Gateway", headers: { "x-request-id": "hdr1" } });
  await assert.rejects(fetchSnapshots(), (e) => {
    assert.deepEqual([e.message, e.code, e.requestId], ["Bad Gateway", "http_error", "hdr1"]);
    return true;
  });
});

test("a server that cannot be reached is reported as a network error", async () => {
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(fetchSnapshots(), (e) => {
    assert.ok(e instanceof ApiError);
    assert.deepEqual([e.code, e.requestId], ["network", null]);
    return true;
  });
});

test("a successful call returns the body", async () => {
  respond(200, [{ snapshot_date: "2026-09-25" }]);
  assert.deepEqual(await fetchSnapshots(), [{ snapshot_date: "2026-09-25" }]);
});

test("comps are asked for by coordinates, with the filters", async () => {
  let asked;
  globalThis.fetch = async (path) => { asked = path; return new Response("{}"); };
  await fetchComps({ latitude: 40.63, longitude: 22.94 }, { bedrooms: 2, radius_m: 800 });
  assert.equal(asked, "/api/comps?lat=40.63&lon=22.94&bedrooms=2&radius_m=800");
});

test("locationOf tells coordinates from an address", () => {
  assert.deepEqual(locationOf(" 40.6326, 22.9416 "), { lat: 40.6326, lon: 22.9416 });
  assert.deepEqual(locationOf("Egnatia 120"), { address: "Egnatia 120" });
});
