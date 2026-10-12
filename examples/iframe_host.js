(async()=>{
 const frame=document.getElementById("intent-agent"), status=document.getElementById("status");
 const config=await fetch("/config",{credentials:"same-origin"}).then(r=>r.json());
 let nonce="",loggedOut=false;
 const post=(path,body)=>fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 window.addEventListener("message",async event=>{
   const data=event.data;
   if(loggedOut||event.origin!==config.platform||event.source!==frame.contentWindow||!data||data.protocol_version!=="1"||data.agent_id!==config.agent_id||typeof data.instance_nonce!=="string"||!data.instance_nonce)return;
   if(data.type==="AGENT_READY"){
     nonce=data.instance_nonce;const localNonce=nonce;status.textContent="正在连接…";
     try{const response=await post("/internal/intent/embed-ticket",{instance_nonce:localNonce});const value=await response.json();if(!response.ok)throw new Error(value.message);if(loggedOut||nonce!==localNonce)return;
       frame.contentWindow.postMessage({type:"AGENT_INIT",protocol_version:"1",agent_id:config.agent_id,instance_nonce:localNonce,ticket:value.ticket,runtime_session_id:value.runtime_session_id},config.platform);
     }catch(e){if(nonce===localNonce)status.textContent=e.message||"暂时无法连接";}
   } else if(data.instance_nonce===nonce&&data.type==="AGENT_AUTHENTICATED"){status.textContent="已连接";}
   else if(data.instance_nonce===nonce&&data.type==="AUTH_REQUIRED"){status.textContent="正在恢复连接…";}
 });
 document.getElementById("logout").onclick=async()=>{
   try{const r=await post("/internal/intent/logout",{});const v=await r.json();if(!r.ok)throw new Error(v.message);
     loggedOut=true;frame.contentWindow.postMessage({type:"HOST_LOGOUT",protocol_version:"1",agent_id:config.agent_id,instance_nonce:nonce},config.platform);status.textContent="已结束访问，刷新宿主页可重新开始。";
   }catch(e){status.textContent=e.message||"退出未确认，请重试";}
 };
 frame.src=config.platform+"/embed/"+encodeURIComponent(config.public_id);
})();