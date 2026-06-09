"""The local admin dashboard (served at GET /).

A single self-contained HTML page. It talks to the localhost-only /admin/*
endpoints to list/create/delete keys and show ready-to-paste connection snippets.
Uses location.origin as the base URL, so nothing needs to be injected server-side.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Misanthropic</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --line:#262b36; --fg:#e6e9ef; --dim:#8b93a7; --acc:#7c9cff; --ok:#3fb950; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:20px 24px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:12px; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--ok); box-shadow:0 0 8px var(--ok); }
  header .meta { color:var(--dim); font-size:13px; margin-left:auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  main { max-width:880px; margin:0 auto; padding:24px; }
  .row { display:flex; gap:10px; align-items:center; margin-bottom:18px; }
  input[type=text] { flex:1; background:var(--card); border:1px solid var(--line); color:var(--fg); padding:10px 12px; border-radius:8px; font-size:14px; }
  button { background:var(--acc); color:#0b0e14; border:0; padding:10px 14px; border-radius:8px; font-weight:600; cursor:pointer; font-size:14px; }
  button.ghost { background:transparent; color:var(--dim); border:1px solid var(--line); font-weight:500; }
  button.ghost:hover { color:var(--fg); }
  .key { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-bottom:12px; }
  .key .top { display:flex; align-items:center; gap:10px; }
  .key .label { font-weight:600; }
  .key .badge { font-size:12px; color:var(--dim); border:1px solid var(--line); border-radius:20px; padding:2px 9px; }
  .key code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; color:var(--acc); }
  .key .actions { margin-left:auto; display:flex; gap:8px; }
  .snippet { margin-top:12px; background:#0b0e14; border:1px solid var(--line); border-radius:8px; padding:12px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; white-space:pre-wrap; color:var(--dim); display:none; }
  .snippet.show { display:block; }
  .empty { color:var(--dim); text-align:center; padding:40px; border:1px dashed var(--line); border-radius:12px; }
  .toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:var(--ok); color:#06270f; padding:8px 16px; border-radius:8px; font-weight:600; opacity:0; transition:opacity .2s; }
  .toast.show { opacity:1; }
  /* recent activity */
  .activity-head { display:flex; align-items:center; gap:10px; margin:36px 0 12px; }
  .activity-head h2 { font-size:12px; margin:0; color:var(--dim); font-weight:600; text-transform:uppercase; letter-spacing:.06em; }
  .activity-head .grow { flex:1; }
  .reqwrap { background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  table.reqs { width:100%; border-collapse:collapse; font-size:12.5px; }
  table.reqs th { text-align:left; color:var(--dim); font-weight:500; padding:8px 12px; border-bottom:1px solid var(--line); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  table.reqs td { padding:8px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
  table.reqs tr:last-child td { border-bottom:0; }
  table.reqs tr.preview td { padding:0 12px 10px 12px; border-bottom:1px solid var(--line); white-space:normal; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px; color:var(--dim); }
  .pv-body { max-height:280px; overflow:auto; }
  .pv-line { display:block; margin-top:4px; white-space:pre-wrap; word-break:break-word; }
  .pv-line.prompt { color:var(--acc); }
  .pv-line.reply { color:var(--ok); }
  .ok { color:var(--ok); }
  .err { color:#ff6b6b; }
  .badge-mode { background:#0b0e14; border:1px solid var(--line); border-radius:4px; padding:1px 7px; font-size:11px; color:var(--dim); }
  .badge-mode.web, .badge-mode.session-web { color:var(--acc); border-color:#2a3a73; }
  .badge-mode.session, .badge-mode.session-web { font-weight:600; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  /* savings banner */
  .savings { background:linear-gradient(135deg,#13241a,#171a21); border:1px solid #234534; border-radius:14px; padding:16px 18px; margin-bottom:20px; }
  .save-big { font-size:16px; font-weight:500; }
  .save-big b { color:var(--ok); font-weight:700; }
  .save-sub { color:var(--dim); font-size:12.5px; margin-top:5px; }
  .save-sub b { color:var(--fg); }
</style>
</head>
<body>
<header>
  <span class="dot"></span>
  <h1>Misanthropic</h1>
  <span class="meta" id="meta">…</span>
</header>
<main>
  <div class="savings" id="savings" hidden></div>
  <div class="row">
    <input type="text" id="label" placeholder="Project name (e.g. my-app)" />
    <button id="create">+ New key</button>
  </div>
  <div id="keys"></div>
  <div class="activity-head">
    <h2>Recent activity</h2>
    <div class="grow"></div>
    <button class="ghost" id="pv-toggle">Show full text</button>
  </div>
  <div id="requests" class="reqwrap"></div>
</main>
<div class="toast" id="toast"></div>
<script>
const $ = s => document.querySelector(s);
const base = location.origin;
function toast(msg){ const t=$("#toast"); t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),1400); }
function copy(text){ navigator.clipboard.writeText(text).then(()=>toast("Copied")); }
function snippet(key){
  return `# shell\nexport ANTHROPIC_BASE_URL=${base}\nexport ANTHROPIC_API_KEY=${key}\n\n`+
         `# python\nfrom anthropic import Anthropic\nclient = Anthropic(base_url="${base}", api_key="${key}")`;
}
async function load(){
  const s = await (await fetch(base+"/admin/state")).json();
  $("#meta").textContent = `${s.mode} · ${base.replace(/^https?:\/\//,'')}`;
  const wrap = $("#keys");
  if(!s.keys.length){ wrap.innerHTML = `<div class="empty">No keys yet. Create one to connect a project.</div>`; return; }
  wrap.innerHTML = s.keys.map(k => `
    <div class="key">
      <div class="top">
        <span class="label">${k.label || "(unnamed)"}</span>
        ${k.turns ? `<span class="badge">${k.turns} turns</span>` : `<span class="badge">new</span>`}
        <div class="actions">
          <button class="ghost" onclick="copy('${k.key}')">Copy key</button>
          <button class="ghost" onclick="document.getElementById('s_${k.key}').classList.toggle('show')">Connect</button>
          <button class="ghost" onclick="del('${k.key}')">Delete</button>
        </div>
      </div>
      <div style="margin-top:6px"><code>${k.key.slice(0,18)}…${k.key.slice(-4)}</code></div>
      <div class="snippet" id="s_${k.key}">${snippet(k.key).replace(/</g,'&lt;')}<br><br><button class="ghost" onclick="copy(\`${snippet(k.key)}\`)">Copy snippet</button></div>
    </div>`).join("");
}
$("#create").onclick = async () => {
  const label = $("#label").value.trim();
  const r = await (await fetch(base+"/admin/keys",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({label})})).json();
  $("#label").value="";
  toast("Key created"); copy(r.key); await load();
};
async function del(key){
  if(!confirm("Delete this key and its conversation?")) return;
  await fetch(base+"/admin/keys/delete",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({key})});
  await load();
}
load();

// --- recent activity --------------------------------------------------
let showText = false;
let lastReqs = [];
function esc(s){ return String(s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function fmtTime(ts){ const d=new Date(ts*1000); return d.toLocaleTimeString([], {hour12:false}); }
function renderRequests(reqs){
  lastReqs = reqs;
  const wrap = $("#requests");
  // Preserve the scroll position of any expanded row across the re-render,
  // keyed by request ts — otherwise rebuilding the table snaps it back to top.
  const scrolls = {};
  wrap.querySelectorAll(".pv-body").forEach(el => { scrolls[el.dataset.ts] = el.scrollTop; });
  const restore = () => wrap.querySelectorAll(".pv-body").forEach(el => {
    const s = scrolls[el.dataset.ts]; if(s) el.scrollTop = s;
  });
  if(!reqs.length){ wrap.innerHTML = `<div class="empty" style="border:0;margin:0">No requests yet. Run a curl or SDK call against the proxy and watch it appear here.</div>`; return; }
  const rows = reqs.map(r => {
    const status = r.status===200 ? `<span class="ok">${r.status}</span>` : `<span class="err">${esc(r.status||"err")}</span>`;
    const tokens = (r.input_tokens!=null||r.output_tokens!=null) ? `<span class="mono">${r.input_tokens||0}→${r.output_tokens||0}</span>` : `<span class="mono" style="color:var(--dim)">—</span>`;
    const dur = r.duration_ms!=null ? `<span class="mono">${r.duration_ms} ms</span>` : "";
    const modeClass = (r.mode||"").replace("+","-");
    const mode = `<span class="badge-mode ${modeClass}">${esc(r.mode||"")}${r.stream?" · stream":""}</span>`;
    let pv = "";
    if(showText){
      const prompt = r.prompt_text ? `<span class="pv-line prompt">› ${esc(r.prompt_text)}</span>` : "";
      const reply = r.response_text ? `<span class="pv-line reply">‹ ${esc(r.response_text)}</span>` : (r.error ? `<span class="pv-line err">‹ ${esc(r.error)}</span>` : "");
      if(prompt || reply) pv = `<tr class="preview"><td colspan="7"><div class="pv-body" data-ts="${r.ts}">${prompt}${reply}</div></td></tr>`;
    }
    return `<tr>
      <td class="mono">${fmtTime(r.ts)}</td>
      <td>${esc(r.key_label||"")}</td>
      <td class="mono">${esc(r.model||"")}</td>
      <td>${mode}</td>
      <td>${tokens}</td>
      <td>${dur}</td>
      <td>${status}</td>
    </tr>${pv}`;
  }).join("");
  wrap.innerHTML = `<table class="reqs"><thead><tr><th>time</th><th>key</th><th>model</th><th>mode</th><th>tokens</th><th>dur</th><th>status</th></tr></thead><tbody>${rows}</tbody></table>`;
  restore();
}
function money(n){
  n = Number(n)||0;
  const d = (n>0 && n<0.01) ? 4 : 2;   // sub-cent precision for tiny totals
  return "$"+n.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
}
function renderSavings(s){
  const el = $("#savings");
  if(!s){ el.hidden = true; return; }
  el.hidden = false;                    // always visible once the server is up
  const n = s.all_time_requests||0;
  if(!n){
    el.innerHTML =
      `<div class="save-big">☠ Your dodged-API-bill counter</div>`+
      `<div class="save-sub">Run a request through the proxy and watch what you'd have `+
      `paid on the hosted API rack up here.</div>`;
    return;
  }
  const since = s.since ? new Date(s.since).toLocaleDateString() : null;
  el.innerHTML =
    `<div class="save-big">☠ You'd have paid <b>${money(s.all_time_usd)}</b> on the API.</div>`+
    `<div class="save-sub">Misanthropic charged you <b>$0.00</b>. `+
    `This month ${money(s.month_usd)} · ${n.toLocaleString()} request${n===1?"":"s"}`+
    (since?` · since ${esc(since)}`:"")+`</div>`;
}
let lastSig = null;
async function pollRequests(){
  try{
    const r = await (await fetch(base+"/admin/requests")).json();
    renderSavings(r.savings);  // always refresh — persists across restarts
    const reqs = r.requests || [];
    // Re-render the table only when the set of requests actually changed;
    // otherwise an open row would lose its scroll position (and any text
    // selection). New records have a fresh ts, so length + newest ts is enough.
    const sig = reqs.length + "|" + (reqs[0] ? reqs[0].ts : "");
    if(sig === lastSig) return;
    lastSig = sig;
    renderRequests(reqs);
  } catch(e){}
}
$("#pv-toggle").onclick = () => {
  showText = !showText;
  $("#pv-toggle").textContent = showText ? "Hide full text" : "Show full text";
  renderRequests(lastReqs);
};
pollRequests();
setInterval(pollRequests, 2000);
</script>
</body>
</html>"""
