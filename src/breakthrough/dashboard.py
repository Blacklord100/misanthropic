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
<title>Breakthrough</title>
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
</style>
</head>
<body>
<header>
  <span class="dot"></span>
  <h1>Breakthrough</h1>
  <span class="meta" id="meta">…</span>
</header>
<main>
  <div class="row">
    <input type="text" id="label" placeholder="Project name (e.g. my-app)" />
    <button id="create">+ New key</button>
  </div>
  <div id="keys"></div>
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
</script>
</body>
</html>"""
