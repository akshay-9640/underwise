// Static showcase: answers the app's /api calls from recorded results, so the real UI runs with no server.
const fileFetch = window.fetch.bind(window);
const reply = (status, data) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const LOCAL_ONLY = "This showcase replays recorded output. Run the app locally to try this (see the README on GitHub).";

window.fetch = async (url, opts = {}) => {
  const method = opts.method || "GET";
  if (url === "/api/health") return fileFetch("results/health.json");
  let m = url.match(/^\/api\/packets\/([\w-]+)$/);
  if (m && method === "GET") return fileFetch(`results/${m[1]}.json`);
  m = url.match(/^\/api\/packets\/([\w-]+)\/ask$/);
  if (m) {
    const answers = await (await fileFetch(`results/${m[1]}.ask.json`)).json();
    const recorded = answers[JSON.parse(opts.body).question];
    return recorded ? reply(200, recorded) : reply(503, { detail: `${LOCAL_ONLY} Recorded answers exist for the suggested questions.` });
  }
  return reply(503, { detail: LOCAL_ONLY });
};

const STATUS_TEXT = { READY_FOR_REVIEW: "Ready for review", NEEDS_REVIEW: "Needs review" };

document.addEventListener("DOMContentLoaded", async () => {
  const picker = document.getElementById("picker");
  for (const p of await (await fileFetch("results/index.json")).json()) {
    const b = el("button", "card packet");
    b.type = "button";
    const top = el("span", "packet-top");
    top.append(el("span", "packet-id", p.id), el("span", `overall ${p.overall === "READY_FOR_REVIEW" ? "ready" : "needs"}`, STATUS_TEXT[p.overall]));
    const rules = el("span", "packet-rules");
    for (const [id, status] of Object.entries(p.rules)) rules.append(el("span", `pill ${status}`, `${id} ${status}`));
    b.append(top, el("span", "packet-name", p.business), el("span", "packet-tests", p.tests), rules);
    b.addEventListener("click", async () => showResult(await api("GET", `/api/packets/${p.id}`)));
    picker.append(b);
  }
});
