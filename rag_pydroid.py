"""Pydroid-compatible RAG — one file, no Node.js, no FastMCP.

pip install PyPDF2 python-dotenv requests   (in Pydroid hammer tool)

Run:  python rag_pydroid.py
Opens: http://localhost:8081
"""

import io, json, logging, os, re, textwrap, time, hashlib, sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# stdlib must be imported BEFORE we mutate sys.path (otherwise agents/ shadows stdlib)
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ── paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

log = logging.getLogger("rag")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ── credentials (from .env you set up earlier) ──────────────────────────────
PINECONE_KEY = os.getenv("PINECONE_API_KEY", "")
NVIDIA_KEY   = os.getenv("NVIDIA_API_KEY", "")
INDEX        = "textbook-rag"

EMBED_URL    = "https://integrate.api.nvidia.com/v1/embeddings"
CHAT_URL     = "https://integrate.api.nvidia.com/v1/chat/completions"
PC_URL = os.getenv("PINECONE_HOST", "")  # e.g. "https://textbook-rag-ixbk0ml.svc.aped-4627-b74a.pinecone.io"

EMBED_MODEL  = "nvidia/nemotron-3-embed-1b"
CHAT_MODEL   = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

# ── PDF ─────────────────────────────────────────────────────────────────────
def read_pdf(data: bytes | str) -> str:
    from PyPDF2 import PdfReader
    if isinstance(data, str):
        data = Path(data).read_bytes()
    pages = []
    for i, p in enumerate(PdfReader(io.BytesIO(data)).pages):
        txt = p.extract_text() or ""
        pages.append(f"[p{i+1}]\n{txt}")
    return "\n\n".join(pages)

# ── chunk ───────────────────────────────────────────────────────────────────
def chunk(text: str, size=4000, step=200):
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i+size])
        i += size - step
    return out

# ── Pinecone helpers ─────────────────────────────────────────────────────────
def pc(path, body):
    r = requests.post(
        f"{PC_URL}{path}",
        headers={"Api-Key": PINECONE_KEY, "Content-Type": "application/json"},
        json=body, timeout=60,
    )
    r.raise_for_status()
    return r.json()

# ── NVIDIA embed ────────────────────────────────────────────────────────────
def embed(texts, input_type="passage"):
    r = requests.post(EMBED_URL,
        headers={"Authorization": f"Bearer {NVIDIA_KEY}", "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": texts, "input_type": input_type}, timeout=120)
    r.raise_for_status()
    return [e["embedding"] for e in sorted(r.json()["data"], key=lambda x: x["index"])]

# ── NVIDIA chat ─────────────────────────────────────────────────────────────
def ask(system_prompt, user_msg, max_tokens=600):
    r = requests.post(CHAT_URL,
        headers={"Authorization": f"Bearer {NVIDIA_KEY}", "Content-Type": "application/json"},
        json={"model": CHAT_MODEL,
              "messages": [{"role":"system","content":system_prompt},
                           {"role":"user","content":user_msg}],
              "max_tokens": max_tokens, "temperature": 0.3},
        timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

# ── ingest ──────────────────────────────────────────────────────────────────
def ingest(pdf_bytes, name, batch=40):
    t0 = time.time()
    raw  = read_pdf(pdf_bytes)
    chks = chunk(raw)
    log.info("[%s] %d chunks from %s", name, len(chks), re.findall(r"\[p\d+\]", raw)[-1] if chks else "?")
    vecs = []
    for i in range(0, len(chks), batch):
        embs = embed(chks[i:i+batch])
        for j, (c, v) in enumerate(zip(chks[i:i+batch], embs)):
            vecs.append({"id": hashlib.sha256(f"{name}::{i+j}".encode()).hexdigest()[:24],
                         "values": v,
                         "metadata": {"textbook": name, "chunk": i+j, "text": c[:8192]}})
    for i in range(0, len(vecs), batch):
        pc("/vectors/upsert", {"vectors": vecs[i:i+batch], "namespace": "default"})
    return {"textbook": name, "chunks": len(vecs), "sec": round(time.time()-t0,1)}

# ── query ───────────────────────────────────────────────────────────────────
def query(question, top_k=5, book=None, username=None):
    if not username:
        session_file = ROOT / "session.json"
        if session_file.exists():
            try:
                session_data = json.loads(session_file.read_text(encoding="utf-8"))
                username = session_data.get("username")
            except Exception:
                pass

    search_query = question
    # Check if the query has first-person pronouns
    if username and re.search(r"\b(i|me|my|myself|we|us|our|ours)\b", question, re.IGNORECASE):
        search_query = f"{username} {question}"

    qv = embed([search_query], input_type="query")[0]
    filt = {"textbook": {"$eq": book}} if book else None
    res = pc("/query", {"vector": qv, "topK": top_k, "includeMetadata": True,
                        "filter": filt, "namespace": "default"})
    matches = res.get("matches", [])
    if not matches:
        return {"answer": "No matching content found. Upload a textbook first.", "sources": []}
    ctx, srcs = [], []
    for i, m in enumerate(matches):
        md = m.get("metadata") or {}
        ctx.append(f"[Source {i+1}: {md.get('textbook','?')}]\n{md.get('text','')}")
        srcs.append({"textbook": md.get("textbook","?"), "chunk": md.get("chunk",i),
                      "score": round(m.get("score",0),3), "excerpt": md.get("text","")[:200]+"…"})
    
    sys_prompt = "Answer the question using ONLY the textbook excerpts below. Cite [Source N]. If the answer isn't there, say so."
    if username:
        sys_prompt += f" The current user asking the question is named '{username}' (referred to as 'I', 'me', 'my', 'myself' in the question)."
    ans = ask(sys_prompt, f"CONTEXT:\n{chr(10).join(ctx)}\n\nQ: {question}")
    return {"answer": ans, "sources": srcs}

# ── HTTP server ─────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>📚 RAG Chat</title>
<style>
*{box-sizing:border-box;margin:0}body{font-family:-apple-system,sans-serif;background:#1a1a2e;color:#eee;min-height:100vh;display:flex;flex-direction-column}
header{background:#16213e;padding:12px 16px;font-size:17px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
header small{opacity:.6;font-size:11px}
.toolbar{padding:8px 12px;background:#16213e;display:flex;gap:8px;flex-wrap:wrap;border-top:1px solid #0f3460}
.toolbar input{flex:1;padding:8px 12px;border-radius:12px;border:1px solid #0f3460;background:#0a0a1a;color:#fff;font-size:13px}
.toolbar input[type=file]{flex:none;width:130px}
.toolbar button{padding:8px 14px;border-radius:12px;border:none;background:#e94560;color:#fff;font-weight:700;cursor:pointer}
#chat{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:82%;padding:10px 14px;border-radius:18px;line-height:1.4;font-size:14px;white-space:pre-wrap}
.user{background:#0f3460;align-self:flex-end}
.assistant{background:#16213e;align-self:flex-start;border:1px solid #0f3460}
.src{font-size:10px;opacity:.55;margin-top:5px;font-style:italic}
footer{padding:10px;display:flex;gap:8px;background:#16213e}
footer input{flex:1;padding:10px;border-radius:20px;border:none;background:#0f3460;color:#fff;font-size:14px}
footer button{padding:10px 18px;border-radius:20px;border:none;background:#e94560;color:#fff;font-weight:700;cursor:pointer</style>
</head>
<body>
<header>📚 RAG Chat <small id="h">&nbsp;</small></header>
<div class="toolbar">
  <input id="bk" placeholder="Textbook name (e.g. Physics)" />
  <input type="file" id="pdf" accept=".pdf" />
  <button id="up">Upload PDF</button>
  <span id="st">&nbsp;</span>
</div>
<div id="chat"><div class="msg assistant" style="align-self:center;opacity:.5">👋 Upload a textbook, then ask!</div></div>
<footer>
  <input id="q" placeholder="Ask anything…" />
  <button id="go">Ask</button>
</footer>
<script>
const chat=document.getElementById('chat'), q=document.getElementById('q'), st=document.getElementById('st');
function add(role,txt,srcs){const d=document.createElement('div');d.className='msg '+role;
if(srcs&&srcs.length){d.innerHTML=txt+'<div class=src>'+srcs.map((s,i)=>`[${i+1}] ${s.textbook} (#${s.chunk}, ${s.score})`).join(' | ')+'</div>'}
else d.textContent=txt;chat.appendChild(d);chat.scrollTop=chat.scrollHeight}
async function call(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});if(!r.ok)throw new Error(await r.text());return r.json()}
document.getElementById('up').onclick=async()=>{const f=document.getElementById('pdf').files[0];if(!f){alert('Pick a PDF');return}
const nm=document.getElementById('bk').value.trim()||f.name.replace('.pdf','');
st.textContent='⏳ Processing…';try{const r=await call('/ingest',{name:nm,pdf:Array.from(new Uint8Array(await f.arrayBuffer()))});st.textContent=`✅ ${r.chunks} chunks in ${r.sec}s`}catch(e){st.textContent='❌ '+e.message}};
document.getElementById('go').onclick=async()=>{const v=q.value.trim();if(!v)return;add('user',v);q.value='';document.getElementById('go').disabled=true;
try{const r=await call('/query',{question:v,textbook:document.getElementById('bk').value.trim()||null});add('assistant',r.answer,r.sources)}catch(e){add('assistant','Error: '+e.message)}document.getElementById('go').disabled=false};
q.addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('go').click()});
fetch('/health').then(r=>r.json()).then(d=>document.getElementById('h').textContent=`pinecone:${d.pinecone||'?'} nvidia:${d.nvidia||'?'}`).catch(()=>{});
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log(self,*a): log.info(*a)
    def _s(self,code,ct,body):
        self.send_response(code); self.send_header("Content-Type",ct)
        self.send_header("Content-Length",str(len(body))); self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(body)
    def _json(self): clen=int(self.headers.get("Content-Length",0)); return json.loads(self.rfile.read(clen)) if clen else {}
    def do_GET(self):
        if self.path in ("/","/index.html"): self._s(200,"text/html",HTML.encode())
        elif self.path=="/health":
            pinecone="ok ("+str(len(PINECONE_KEY))+"ch)" if PINECONE_KEY else "MISSING"
            nvidia="ok" if NVIDIA_KEY else "MISSING"
            self._s(200,"application/json",json.dumps({"pinecone":pinecone,"nvidia":nvidia}).encode())
        else: self._s(404,"text/plain",b"nope")
    def do_POST(self):
        try:
            body=self._json()
            if self.path=="/ingest":
                res=ingest(bytes(body.get("pdf",[])), body.get("name","Book"), batch=40)
                self._s(200,"application/json",json.dumps(res).encode())
            elif self.path=="/query":
                res=query(body.get("question",""), top_k=5, book=body.get("textbook"), username=body.get("username"))
                self._s(200,"application/json",json.dumps(res).encode())
            else: self._s(404,"text/plain",b"nope")
        except Exception as e:
            log.error("%s %s",self.path,e); self._s(500,"application/json",json.dumps({"error":str(e)}).encode())

serve = ThreadingHTTPServer(("0.0.0.0",8081), H)

if __name__ == "__main__":
    log.info("🚀 RAG on http://localhost:8081  (Ctrl+C to stop)")
    try: serve.serve_forever()
    except KeyboardInterrupt: log.info("bye"); serve.server_close()
