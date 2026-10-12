"""Local, single-app example host. Its Key and visitor/session map never enter browser code.
Run with INTENT_HOST_KEY, INTENT_HOST_AGENT, INTENT_HOST_PUBLIC_ID set on the server.
The in-memory visitor map is for this local example; real hosts persist it in their session store.
"""
import json
import os
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import httpx

PLATFORM = os.environ.get("INTENT_HOST_PLATFORM", "http://127.0.0.1:5173").rstrip("/")
API = os.environ.get("INTENT_HOST_API", PLATFORM).rstrip("/")
ORIGIN = os.environ.get("INTENT_HOST_ORIGIN", "http://127.0.0.1:5175").rstrip("/")
KEY = os.environ["INTENT_HOST_KEY"]
AGENT = os.environ["INTENT_HOST_AGENT"]
PUBLIC = os.environ["INTENT_HOST_PUBLIC_ID"]
VISITORS = {}
LOCK = threading.Lock()
HOST = urlsplit(ORIGIN)
if HOST.hostname not in {"127.0.0.1", "localhost"} or HOST.scheme != "http":
    raise RuntimeError("This example binds only a local HTTP host; deploy a trusted host separately")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Request bodies, keys and cookies are never logged.

    def send(self, status, data, mime="application/json", cookie=None):
        raw = data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy",f"default-src 'self'; script-src 'self'; style-src 'self'; frame-src {PLATFORM}; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
        if cookie:self.send_header("Set-Cookie",cookie)
        self.end_headers(); self.wfile.write(raw)

    def visitor(self,create=False):
        cookies=SimpleCookie()
        try:cookies.load(self.headers.get("Cookie",""))
        except Exception:pass
        sid=cookies["intent_host"].value if "intent_host" in cookies else ""
        with LOCK:
            if sid not in VISITORS:
                if not create:return None,None
                sid=secrets.token_urlsafe(32)
                VISITORS[sid]={"visitor_id":secrets.token_urlsafe(32),"runtime_session_id":None,"lock":threading.Lock()}
            return sid,VISITORS[sid]

    def do_GET(self):
        if self.path=="/":
            sid,_=self.visitor(True)
            self.send(200,Path(__file__).with_name("iframe_host.html").read_bytes(),"text/html; charset=utf-8",f"intent_host={sid}; Path=/; HttpOnly; SameSite=Lax")
        elif self.path=="/host.js":self.send(200,Path(__file__).with_name("iframe_host.js").read_bytes(),"text/javascript; charset=utf-8")
        elif self.path=="/host.css":self.send(200,Path(__file__).with_name("iframe_host.css").read_bytes(),"text/css; charset=utf-8")
        elif self.path=="/config":self.send(200,{"platform":PLATFORM,"agent_id":AGENT,"public_id":PUBLIC})
        else:self.send(404,{"message":"Not found"})

    def do_POST(self):
        if self.headers.get("Origin")!=ORIGIN or self.headers.get("Content-Type","").split(";")[0]!="application/json":
            return self.send(403,{"message":"宿主请求来源不匹配"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=8192:raise ValueError()
            body=json.loads(self.rfile.read(size))
        except (ValueError,TypeError):return self.send(400,{"message":"请求格式不正确"})
        sid,visitor=self.visitor()
        if not visitor:return self.send(401,{"message":"请刷新宿主页创建访客会话"})
        with visitor["lock"]:
            try:
                with httpx.Client(base_url=API,timeout=12,trust_env=False) as client:
                    headers={"Authorization":"Bearer "+KEY}
                    if self.path=="/internal/intent/embed-ticket":
                        nonce=body.get("instance_nonce")
                        if not isinstance(nonce,str) or not nonce or len(nonce)>256:return self.send(422,{"message":"握手信息无效"})
                        # Never trust a visitor_id, agent_id, Origin or session ID submitted by the browser.
                        response=client.post("/api/v2/integrations/embed-tickets",headers=headers,json={"agent_id":AGENT,"host_origin":ORIGIN,"instance_nonce":nonce,"visitor_id":visitor["visitor_id"],"runtime_session_id":visitor["runtime_session_id"]})
                        if response.status_code!=200:return self.send(response.status_code,{"message":"平台拒绝换票，请检查账号权限、Key 或宿主配置"})
                        value=response.json();visitor["runtime_session_id"]=value["runtime_session_id"]
                        return self.send(200,{"ticket":value["ticket"],"runtime_session_id":value["runtime_session_id"]})
                    if self.path=="/internal/intent/logout":
                        runtime=visitor["runtime_session_id"]
                        if runtime:
                            response=client.post(f"/api/v2/integrations/runtime-sessions/{runtime}/revoke",headers=headers,json={"visitor_id":visitor["visitor_id"]})
                            if response.status_code not in {200,401,403}:return self.send(502,{"message":"平台退出尚未确认，请重试"})
                        with LOCK:VISITORS.pop(sid,None)
                        return self.send(200,{"revoked":True},cookie="intent_host=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
                    return self.send(404,{"message":"Not found"})
            except (httpx.HTTPError,ValueError,KeyError):self.send(502,{"message":"平台暂不可用，请稍后重试"})

if __name__=="__main__":
    print("Local example host ready at "+ORIGIN,flush=True)
    ThreadingHTTPServer((os.environ.get("INTENT_HOST_BIND",HOST.hostname),HOST.port or 5175),Handler).serve_forever()
