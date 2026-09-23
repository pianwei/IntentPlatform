"""Local OpenAPI reader with no CDN, font, telemetry or external script dependency."""

OPENAPI_PAGE = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>意图中台 · API 契约</title>
<style>
body{font:15px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#17324d;background:#f7f9fb}
h1{margin-bottom:8px}a{color:#007c83}input{box-sizing:border-box;width:100%;padding:12px;border:1px solid #bdcedd;border-radius:8px;margin:20px 0}
details{background:white;border:1px solid #dce5ee;border-radius:8px;margin:12px 0;padding:14px}summary{cursor:pointer;overflow-wrap:anywhere}
code{font-size:13px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7fa;padding:14px;font-size:12px}
.method{display:inline-block;min-width:55px;font-weight:700;color:#00888b}.note{color:#5b6e80}#error{color:#a42a36}
</style>
<h1>API 契约</h1><p class="note">管理、运行与宿主集成接口。文档资源完全在本地加载；接口调用仍受服务端身份与空间授权控制。</p>
<p><a href="/openapi.json" download="intent-openapi.json">下载完整 OpenAPI JSON</a> · <span id="version"></span></p>
<label for="filter">筛选接口</label><input id="filter" type="search" placeholder="路径、方法或名称">
<p id="error" role="alert"></p><main id="operations"></main><h2>共享数据类型</h2><section id="schemas"></section>
<script src="/docs/app.js" defer></script></html>"""

OPENAPI_SCRIPT = """
const blocks=[];
function add(parent,title,value){const d=document.createElement('details');const s=document.createElement('summary');s.textContent=title;d.append(s);const p=document.createElement('pre');p.textContent=JSON.stringify(value,null,2);d.append(p);parent.append(d);return d;}
fetch('/openapi.json',{credentials:'omit'}).then(r=>{if(!r.ok)throw Error('无法读取契约：'+r.status);return r.json();}).then(spec=>{
 document.getElementById('version').textContent='软件 '+spec.info.version+' · OpenAPI '+spec.openapi;
 for(const [path,entry] of Object.entries(spec.paths)){for(const [method,op] of Object.entries(entry)){if(!['get','post','put','patch','delete','options','head'].includes(method))continue;const title=method.toUpperCase()+' '+path+' · '+(op.summary||'');const d=add(document.getElementById('operations'),title,op);blocks.push([d,title.toLowerCase()]);}}
 for(const [name,schema] of Object.entries(spec.components?.schemas||{}))add(document.getElementById('schemas'),name,schema);
}).catch(e=>document.getElementById('error').textContent=e.message);
document.getElementById('filter').addEventListener('input',e=>{const value=e.target.value.toLowerCase();for(const [block,title] of blocks)block.hidden=!title.includes(value);});
"""
