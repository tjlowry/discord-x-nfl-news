// Cloudflare Worker: triggers the `poll` GitHub Actions workflow on a cron.
// GitHub's own `schedule:` trigger is unreliable, so this does the scheduling.
//
// Secret:  GH_TOKEN  fine-grained PAT, this repo only, Actions: read & write
// Trigger: cron "7 13,18,23 * * *" (set in wrangler.toml or the dashboard)
//
// No fetch handler on purpose: the Worker's URL can't be used to trigger runs.

const REPO = "tjlowry/discord-x-nfl-news";
const WORKFLOW = "poll.yml";
const REF = "main";

export async function dispatch(env, fetchImpl = fetch) {
  if (!env.GH_TOKEN) {
    throw new Error("GH_TOKEN secret is not set");
  }
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const resp = await fetchImpl(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GH_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "discord-x-bot-cron",
    },
    body: JSON.stringify({ ref: REF }),
  });
  if (resp.status !== 204) {
    const body = (await resp.text()).slice(0, 300);
    const hint =
      resp.status === 401 ? " (token invalid or expired: make a new one and update GH_TOKEN)" :
      resp.status === 403 ? " (token lacks Actions: read & write on this repo)" :
      resp.status === 404 ? " (wrong repo/workflow name, or token can't see the repo)" : "";
    // Throwing marks the cron invocation as failed in the Cloudflare dashboard.
    throw new Error(`GitHub dispatch failed: HTTP ${resp.status}${hint}: ${body}`);
  }
  console.log(`dispatched ${WORKFLOW} on ${REF}`);
}

export default {
  async scheduled(controller, env, ctx) {
    await dispatch(env);
  },
};
