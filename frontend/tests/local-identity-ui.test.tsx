import { StrictMode } from "react";
import { cleanup,fireEvent,render,screen,waitFor } from "@testing-library/react";
import { afterEach,expect,it,vi } from "vitest";
import { ConsoleApp } from "../console/src/App";
import { LocalLogin } from "../console/src/LocalLogin";
import { PersonalCenter } from "../console/src/PersonalCenter";
import { AgentApp } from "../agent-web/src/App";
import { ApiClient } from "../shared/api";
import { runtimeUI, readAnonymous, saveAnonymous } from "../shared/application";
import type { Profile } from "../shared/accounts";
vi.mock("../agent-web/src/AgentWorkspace",()=>({AgentWorkspace:({onLogout}:{onLogout:()=>Promise<void>})=><section><h1>公开业务问答</h1><button onClick={()=>void onLogout()}>结束访问</button></section>}));
const response=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status});
afterEach(()=>{cleanup();vi.unstubAllGlobals();localStorage.clear();sessionStorage.clear();history.replaceState({},"","/console/");});
const profile:Profile={user_id:"user-a",username:"zhang",display_name:"张明",scope:"management",workspaces:[{id:"ws",name:"客户服务",roles:["admin","user"]}],fixture_mode:false,is_platform_admin:false,must_change_password:false,auth_method:"password",preferences:{density:"comfortable"}};
it("boots local login once in StrictMode without developer or SSO controls",async()=>{
 history.replaceState({},"","/console/");const fetch=vi.fn().mockResolvedValue(response({detail:"No session"},401));vi.stubGlobal("fetch",fetch);
 render(<StrictMode><ConsoleApp/></StrictMode>);await screen.findByRole("heading",{name:"登录管理平台"});
 expect(fetch).toHaveBeenCalledOnce();expect(screen.queryByText(/登录已失效/)).toBeNull();
 expect(screen.queryByLabelText(/可信身份/)).toBeNull();expect(screen.queryByText(/dev-admin/)).toBeNull();
});
it("submits the password without storing it or the access token",async()=>{
 const api=new ApiClient("management");const post=vi.spyOn(api,"post").mockResolvedValue({access_token:"access-not-persisted",user_id:"u"});const accepted=vi.fn();const store=vi.spyOn(Storage.prototype,"setItem");
 render(<LocalLogin api={api} onLogin={accepted}/>);fireEvent.change(screen.getByLabelText("用户名"),{target:{value:"alice"}});fireEvent.change(screen.getByLabelText("密码"),{target:{value:"test-only-password"}});fireEvent.submit(screen.getByRole("button",{name:"登录"}).closest("form")!);
 await waitFor(()=>expect(accepted).toHaveBeenCalled());expect(post).toHaveBeenCalledWith("/api/v2/auth/login",{username:"alice",password:"test-only-password"});expect(store).not.toHaveBeenCalled();
});
it("validates password repetition and exchanges a one-time setup credential",async()=>{
 const api=new ApiClient("management"),post=vi.spyOn(api,"post").mockResolvedValue({changed:true});render(<LocalLogin api={api} onLogin={vi.fn()}/>);
 fireEvent.click(screen.getByText("首次使用或管理员已重置密码"));fireEvent.change(screen.getByLabelText("一次性设置凭证"),{target:{value:"one-time-setup"}});fireEvent.change(screen.getByLabelText("新密码"),{target:{value:"strong-test-password"}});fireEvent.change(screen.getByLabelText("确认新密码"),{target:{value:"different"}});
 fireEvent.submit(screen.getByRole("button",{name:"设置密码"}).closest("form")!);await screen.findByText("两次密码不一致");expect(post).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText("确认新密码"),{target:{value:"strong-test-password"}});fireEvent.submit(screen.getByRole("button",{name:"设置密码"}).closest("form")!);await screen.findByText("密码已设置，请登录。");expect(post).toHaveBeenCalledWith("/api/v2/auth/set-password",{setup_token:"one-time-setup",new_password:"strong-test-password"});expect(screen.queryByLabelText("一次性设置凭证")).toBeNull();
});
it("shows a newly created Key once and does not persist the secret",async()=>{
 const api=new ApiClient("management"),get=vi.spyOn(api,"get").mockResolvedValue([]),post=vi.spyOn(api,"post").mockResolvedValue({key:{id:"key-a"},secret:"synthetic-key-visible-once"});const store=vi.spyOn(Storage.prototype,"setItem");
 render(<PersonalCenter api={api} profile={profile} initialTab="API Key" onProfile={vi.fn()} onClose={vi.fn()} onPasswordChanged={vi.fn()}/>);
 await waitFor(()=>expect(get).toHaveBeenCalled());fireEvent.click(screen.getByRole("button",{name:"创建 Key"}));fireEvent.change(screen.getByLabelText(/名称/),{target:{value:"业务门户"}});fireEvent.submit(screen.getByRole("button",{name:"创建并显示一次"}).closest("form")!);
 expect(await screen.findByLabelText("一次性凭据")).toHaveValue("synthetic-key-visible-once");expect(post).toHaveBeenCalledWith("/api/v2/me/api-keys",{name:"业务门户",expires_at:null});expect(store).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:"已保存，关闭"}));expect(screen.queryByLabelText("一次性凭据")).toBeNull();
});
it("opens public Chat anonymously and restores only its own app-scoped credential",async()=>{
 history.replaceState({},"","/chat/public-a");saveAnonymous("public-a","session-a","recovery-a");saveAnonymous("public-b","session-b","recovery-b");
 const fetch=vi.fn((path:string,options?:RequestInit)=>Promise.resolve(response(path.endsWith("/anonymous-sessions")?{access_token:"memory-access",user_id:"guest-a",runtime_session_id:"session-a",fixture_mode:true}:path.endsWith("/logout")?{revoked:true}:{public_id:"public-a",agent_id:"agent-a",release_id:"release",runtime_ui:runtimeUI(),channels:{chat:true,embed:false}})));vi.stubGlobal("fetch",fetch);
 render(<AgentApp/>);await screen.findByRole("heading",{name:"公开业务问答"});const auth=fetch.mock.calls.find(([p])=>p.endsWith("/anonymous-sessions"))!;
 expect(JSON.parse(String(auth[1]?.body))).toEqual({runtime_session_id:"session-a",recovery_secret:"recovery-a"});expect(JSON.stringify(localStorage)).not.toContain("memory-access");expect(fetch.mock.calls.some(([p])=>p.includes("/auth/")||p.includes("/dev/"))).toBe(false);
 fireEvent.click(screen.getByRole("button",{name:"结束访问"}));await screen.findByRole("button",{name:"连接会话"});expect(readAnonymous("public-a")).toEqual({});expect(readAnonymous("public-b").runtime_session_id).toBe("session-b");
});
it("does not silently create a new anonymous identity when recovery is rejected",async()=>{
 history.replaceState({},"","/chat/public-a");saveAnonymous("public-a","old-session","wrong-secret");
 const fetch=vi.fn((path:string)=>Promise.resolve(path.endsWith("/anonymous-sessions")?response({detail:"恢复凭据已失效"},403):response({public_id:"public-a",agent_id:"agent-a",release_id:"r",runtime_ui:runtimeUI(),channels:{chat:true,embed:false}})));vi.stubGlobal("fetch",fetch);
 render(<AgentApp/>);await screen.findByText("恢复凭据已失效");expect(fetch.mock.calls.filter(([p])=>p.endsWith("/anonymous-sessions"))).toHaveLength(1);expect(screen.queryByRole("heading",{name:"公开业务问答"})).toBeNull();expect(screen.getByRole("button",{name:"放弃恢复，重新开始"})).toBeVisible();
});
it.each(["Anonymous session unavailable", "Anonymous recovery credential unavailable"])("explains rejected recovery (%s) and starts fresh only after an explicit action",async(detail)=>{
 history.replaceState({},"","/chat/public-a");saveAnonymous("public-a","expired-session","expired-secret");saveAnonymous("public-b","other-session","other-secret");
 const fetch=vi.fn((path:string,options?:RequestInit)=>Promise.resolve(path.endsWith("/anonymous-sessions")
   ? JSON.parse(String(options?.body)).runtime_session_id
     ? response({detail},403)
     : response({access_token:"new-memory-access",user_id:"guest-new",runtime_session_id:"new-session",recovery_secret:"new-recovery",fixture_mode:true})
   : response({public_id:"public-a",agent_id:"agent-a",release_id:"r",runtime_ui:runtimeUI(),channels:{chat:true,embed:false}})));vi.stubGlobal("fetch",fetch);
 render(<AgentApp/>);await screen.findByText(/此前的会话无法恢复/);
 expect(readAnonymous("public-a").runtime_session_id).toBe("expired-session");expect(fetch.mock.calls.filter(([p])=>p.endsWith("/anonymous-sessions"))).toHaveLength(1);
 fireEvent.click(screen.getByRole("button",{name:"放弃恢复，重新开始"}));await screen.findByRole("heading",{name:"公开业务问答"});
 expect(fetch.mock.calls.filter(([p])=>p.endsWith("/anonymous-sessions")).map(([,o])=>JSON.parse(String(o?.body)))).toEqual([{runtime_session_id:"expired-session",recovery_secret:"expired-secret"},{}]);
 expect(readAnonymous("public-a").runtime_session_id).toBe("new-session");expect(readAnonymous("public-b").runtime_session_id).toBe("other-session");
 expect(fetch.mock.calls.some(([p])=>/\/messages|\/runs/.test(p))).toBe(false);expect(JSON.stringify(localStorage)).not.toContain("new-memory-access");
});
it("does not mislabel a closed public channel as an expired recovery credential",async()=>{
 history.replaceState({},"","/chat/public-a");saveAnonymous("public-a","session-a","secret-a");
 vi.stubGlobal("fetch",vi.fn((path:string)=>Promise.resolve(path.endsWith("/anonymous-sessions")?response({detail:"Public chat is disabled"},403):response({public_id:"public-a",agent_id:"agent-a",release_id:"r",runtime_ui:runtimeUI(),channels:{chat:true,embed:false}}))));
 render(<AgentApp/>);await screen.findByText("Public chat is disabled");expect(screen.queryByText(/此前的会话无法恢复/)).toBeNull();expect(readAnonymous("public-a").runtime_session_id).toBe("session-a");
});
it("explains legacy publication without retry loops, history loss or management controls",async()=>{
 history.replaceState({},"","/chat/public-a");saveAnonymous("public-a","existing-session","existing-secret");
 const fetch=vi.fn((path:string)=>Promise.resolve(path.endsWith("/anonymous-sessions")?response({detail:"Legacy release is available only in management history; publish a local identity release"},403):response({public_id:"public-a",agent_id:"agent-a",release_id:"legacy",runtime_ui:runtimeUI(),channels:{chat:true,embed:false}})));vi.stubGlobal("fetch",fetch);
 render(<AgentApp/>);await screen.findByText(/发布版本使用旧版访问配置/);
 expect(screen.queryByRole("button",{name:"连接会话"})).toBeNull();expect(screen.queryByRole("button",{name:"放弃恢复，重新开始"})).toBeNull();
 expect(screen.queryByText(/Legacy release/)).toBeNull();expect(screen.queryByRole("link",{name:/管理/})).toBeNull();
 expect(readAnonymous("public-a").runtime_session_id).toBe("existing-session");
 expect(fetch.mock.calls.filter(([p])=>p.endsWith("/anonymous-sessions"))).toHaveLength(1);expect(fetch.mock.calls.some(([p])=>p.includes("/management/")||p.includes("/messages"))).toBe(false);
});
it("ends access cleanly when the host already revoked the runtime identity",async()=>{
 history.replaceState({},"","/chat/public-a");
 const fetch=vi.fn((path:string)=>Promise.resolve(path.endsWith("/logout")?response({detail:"Session expired or revoked"},401):response(path.endsWith("/anonymous-sessions")?{access_token:"memory-access",user_id:"guest-a",runtime_session_id:"session-a",recovery_secret:"one-browser-secret",fixture_mode:true}:{public_id:"public-a",agent_id:"agent-a",release_id:"r",runtime_ui:runtimeUI(),channels:{chat:true,embed:false}})));vi.stubGlobal("fetch",fetch);
 render(<AgentApp/>);await screen.findByRole("heading",{name:"公开业务问答"});fireEvent.click(screen.getByRole("button",{name:"结束访问"}));
 await screen.findByText("访问已结束，请重新连接。");expect(screen.queryByText(/退出未确认/)).toBeNull();expect(readAnonymous("public-a")).toEqual({});expect(fetch.mock.calls.filter(([p])=>p.endsWith("/anonymous-sessions"))).toHaveLength(1);
});
it("renews a rejected management credential once without persisting it",async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(response({detail:"expired"},401)).mockResolvedValueOnce(response({access_token:"renewed"})).mockResolvedValueOnce(response({name:"profile"}));vi.stubGlobal("fetch",fetch);const store=vi.spyOn(Storage.prototype,"setItem");const api=new ApiClient("management");api.setToken("expired");
 expect(await api.get("/api/v2/me")).toEqual({name:"profile"});expect(fetch).toHaveBeenCalledTimes(3);expect(fetch.mock.calls[1][0]).toBe("/api/v2/auth/refresh");expect(fetch.mock.calls[1][1].credentials).toBe("same-origin");expect(fetch.mock.calls[2][1].headers.get("Authorization")).toBe("Bearer renewed");expect(store).not.toHaveBeenCalled();
});
