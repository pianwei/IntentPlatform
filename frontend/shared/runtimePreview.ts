import { ApiClient } from "./api";
import { appPath } from "./application";
import type { Identity } from "./types";
// Console creates a runtime-only identity. No management token is sent to the child.
export function openRuntimePreview(api:ApiClient,publicId:string) {
 const child=window.open(appPath(publicId)+"?preview=1","_blank");
 if(!child)throw new Error("请允许打开测试窗口");
 let session:string|undefined,request=0;
 const listener=async(e:MessageEvent)=>{
   if(e.origin!==location.origin||e.source!==child||e.data?.type!=="INTENT_PREVIEW_READY"||e.data.public_id!==publicId)return;
   const epoch=++request;
   // The server verifies management ownership of any requested runtime session.
   const requested = typeof e.data.runtime_session_id === "string" ? e.data.runtime_session_id : session;
   try{const identity=await api.post<Identity>(`/api/v2/runtime/apps/${encodeURIComponent(publicId)}/sessions`,{runtime_session_id:requested});
     if(epoch!==request||child.closed)return;session=identity.runtime_session_id;child.postMessage({type:"INTENT_PREVIEW_IDENTITY",public_id:publicId,identity},location.origin);
   }catch{if(!child.closed)child.postMessage({type:"INTENT_PREVIEW_ERROR",public_id:publicId},location.origin);}
 };
 window.addEventListener("message",listener);
 const cleanup=()=>{++request;window.removeEventListener("message",listener);clearInterval(timer);};
 const timer=setInterval(()=>{if(child.closed)cleanup();},2000);
 window.addEventListener("pagehide",cleanup,{once:true});
}
