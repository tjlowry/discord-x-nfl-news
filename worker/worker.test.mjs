// Run: node --test worker/
import { test } from "node:test";
import assert from "node:assert/strict";
import { dispatch } from "./worker.js";

const fakeFetch = (status, body = "") => {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    return { status, text: async () => body };
  };
  fn.calls = calls;
  return fn;
};

test("posts a workflow_dispatch for main with the token", async () => {
  const f = fakeFetch(204);
  await dispatch({ GH_TOKEN: "t0k" }, f);
  assert.equal(f.calls.length, 1);
  const { url, init } = f.calls[0];
  assert.equal(url, "https://api.github.com/repos/tjlowry/discord-x-nfl-news/actions/workflows/poll.yml/dispatches");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, "Bearer t0k");
  assert.ok(init.headers["User-Agent"]);
  assert.deepEqual(JSON.parse(init.body), { ref: "main" });
});

test("throws with a hint when the token is expired", async () => {
  await assert.rejects(dispatch({ GH_TOKEN: "old" }, fakeFetch(401, "Bad credentials")),
    /HTTP 401.*expired.*Bad credentials/);
});

test("throws when the secret is missing", async () => {
  await assert.rejects(dispatch({}, fakeFetch(204)), /GH_TOKEN/);
});
