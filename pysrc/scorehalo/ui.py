"""Serve the per-page review UI from an out dir. Stdlib only (KISS)."""

import json
import os
import http.server
import socketserver
import urllib.parse

PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>ScoreHalo review</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#14161a;color:#ddd}
 header{background:#0d0e11;padding:12px 20px;position:sticky;top:0;border-bottom:1px solid #333}
 h1{font-size:16px;margin:0} .sub{color:#8a93a3;font-size:12px;margin-top:2px}
 main{padding:20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
 .card{background:#1c1f26;border:1px solid #2a2e37;border-radius:10px;overflow:hidden}
 .card img{width:100%;max-height:340px;object-fit:cover;background:#000}
 .card .b{padding:10px 12px}
 .tag{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600}
 .ok{background:#1d4f2a;color:#7ce38b} .warn{background:#5f4a10;color:#ffd570}
 .skip{background:#333;color:#bbb} .fail{background:#581414;color:#ff9494}
 .meta{font-size:12px;color:#9aa3b2;margin:6px 0}
 a{color:#7cc} .row{display:flex;gap:10px;font-size:12px;margin-top:6px}
 .err{color:#ff9494;font-size:12px} pre{font-size:11px;overflow:auto;max-height:120px;background:#101216;padding:8px;border-radius:6px}
</style></head><body>
<header><h1>ScoreHalo review</h1><div class="sub" id="hdr"></div></header>
<main id="grid"></main>
<script>
const data = __DATA__;
let pages = data.pages || [];
const counts = {ok:0, warn:0, fail:0, skip:0};
document.getElementById('hdr').textContent = data.source + "  |  " + pages.length + " pages";
const klass = s => s==='ok'?'ok':(s==='validate-warn'||s==='homr-failed'?'warn':(s.startsWith('skip')?'skip':'fail'));
const grid = document.getElementById('grid');
pages.forEach(p => {
  const el = document.createElement('div');
  el.className = 'card';
  const img = p.image ? `<img src="file?p=${encodeURIComponent(p.image)}">` : '';
  const stats = p.stats && typeof p.stats === 'object'
    ? `ink ${p.stats['ink%']}% hline ${p.stats['h_line%']}% inter ${p.stats['interline_est_px']}px` : (p.stats || '');
  const v = p.validation || {};
  const errs = v['errors'] && v['errors'].length ? `<div class="err">${v['errors'].join('<br>')}</div>` : '';
  const notes = v['notes']!=null ? `notes ${v['notes']} | measures ${v['measures']} | parts ${v['parts']}` : '';
  const dl = p.output ? `<a href="file?p=${encodeURIComponent(p.output)}">download .musicxml</a>` : '';
  const log = p.log ? `<details><summary>homr log</summary><pre class="log" data-log="${encodeURIComponent(p.log)}"></pre></details>` : '';
  el.innerHTML = `${img}<div class="b"><div><span class="tag ${klass(p.status)}">${p.status}</span></div>
    <div class="meta">page ${p.page} &middot; ${stats}</div>
    <div class="meta">${notes}</div>${errs}<div class="row">${dl} <a href="file?p=${encodeURIComponent(p.image)}">view</a> ${log}</div></div>`;
  grid.appendChild(el);
});
document.addEventListener('click', ev => { const pre = ev.target.closest('details')?.querySelector('pre[data-log]'); if (!pre || pre.textContent) return; fetch('file?p=' + pre.dataset.log).then(r => r.text()).then(t => { pre.textContent = t; }); });
</script></body></html>
"""


def _load_manifest(out_dir):
    man_path = os.path.join(out_dir, "manifest.json")
    if os.path.exists(man_path):
        with open(man_path) as fh:
            return json.load(fh)
    # fallback: synthesize from files on disk
    import re
    pages = []
    for f in sorted(os.listdir(out_dir)):
        m = re.match(r"p(\d+)\.png$", f)
        if m:
            pages.append({"page": int(m.group(1)), "image": os.path.join(out_dir, f),
                          "status": "ok" if os.path.exists(os.path.join(out_dir, f"p{m.group(1).zfill(4)}.musicxml")) else "?"})
    return {"source": out_dir, "pages": pages}


def serve_ui(out_dir, port=8001, render_root="/"):
    data = _load_manifest(out_dir)
    os.chdir(out_dir)  # serve all files relative to the out dir

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                body = PAGE_HTML.replace("__DATA__", json.dumps(data)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/file":
                q = urllib.parse.parse_qs(parsed.query)
                fp = q.get("p", [""])[0]
                fp = os.path.abspath(fp)
                if not fp.startswith(os.getcwd()):
                    self.send_response(403); self.end_headers(); return
                try:
                    with open(fp, "rb") as fh:
                        body = fh.read()
                except OSError:
                    self.send_response(404); self.end_headers(); return
                ctype = "image/png" if fp.endswith((".png", ".jpg", ".jpeg")) else "application/xml"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404); self.end_headers()

        def log_message(self, fmt, *args):  # quiet
            pass

    with socketserver.TCPServer(("", port), Handler) as httpd:
        print(f"ScoreHalo review UI: http://127.0.0.1:{port}  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0