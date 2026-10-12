import { StrictMode, useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CreationFlow } from '../console/src/CreationFlow';
import { ModelEditor } from '../console/src/ModelSettingsPage';
import { FunctionEditor, blankDefinition } from '../console/src/FunctionEditor';
import { ApiClient } from '../shared/api';
import type { Capability } from '../shared/types';
afterEach(()=>cleanup());
it('opening the capability picker never submits the agent creation form',async()=>{
 const api=fixture(),post=vi.spyOn(api,'post');
 render(<CreationFlow api={api} workspace="ws" session={{key:2,kind:'agent',origin:'/spaces/ws/agents',expanded:false}} functions={[{...draft,status:'published',latest_version:1}]} admin={false} publishable onStay={vi.fn()} onClose={vi.fn()} onAgent={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} onExpand={vi.fn()} onRoute={vi.fn()} onSaved={async()=>{}}/>);
 fireEvent.change(screen.getByLabelText('智能体名称'),{target:{value:'可创建的助手'}});
 fireEvent.click(screen.getByRole('button',{name:'添加功能'}));
 const choice=await screen.findByRole('checkbox',{name:'选择功能 新余额'});
 await waitFor(()=>expect(choice).toBeEnabled()); fireEvent.click(choice);
 fireEvent.click(screen.getByRole('button',{name:/确认添加/}));
 expect(screen.getByRole('button',{name:'创建智能体'})).toBeEnabled();
 fireEvent.click(screen.getByRole('button',{name:'添加功能'}));
 expect(post).not.toHaveBeenCalled();
});
const draft:Capability={id:'new-function',workspace_id:'ws',name:'新余额',description:'读取余额',definition:{...blankDefinition,name:'新余额',description:'读取余额'},revision:1,status:'draft',latest_version:0,is_fixture:true};
function fixture(){const api=new ApiClient('management');vi.spyOn(api,'get').mockImplementation(async p=>p.endsWith('/functions/new-function/versions')?[{id:'fv1',function_id:'new-function',version:1,definition:draft.definition}] as never:p.endsWith('/source-review')?{revision:1,ready:true,required:false,issues:[],citations:[]} as never:p.endsWith('/catalogs')?{integrations:[],credentials:[],resolvers:[]} as never:[] as never);return api;}
it('keeps the same form, validation, step and test values after creation and expansion',async()=>{
 const api=fixture(),route=vi.fn();const post=vi.spyOn(api,'post').mockImplementation(async p=>p.endsWith('/import')?draft as never:{valid:true,revision:1,errors:[],checks:{},execution_test:{passed:true,revision:1,status:"SUCCEEDED"}} as never);
 function Harness(){const [expanded,setExpanded]=useState(false);return <CreationFlow api={api} workspace="ws" session={{key:1,kind:'function',origin:'/spaces/ws/functions',expanded}} functions={[]} admin={false} publishable onStay={vi.fn()} onClose={vi.fn()} onAgent={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} onExpand={()=>setExpanded(true)} onRoute={route} onSaved={async()=>{}}/>;}
 render(<StrictMode><Harness/></StrictMode>);
 fireEvent.change(screen.getByLabelText('功能名称'),{target:{value:'新余额'}});fireEvent.change(screen.getByLabelText('业务描述'),{target:{value:'读取余额'}});
 fireEvent.click(screen.getByRole('button',{name:'下一步'}));fireEvent.click(screen.getByRole('button',{name:'下一步'}));
 fireEvent.change(screen.getByLabelText('契约测试参数（JSON）'),{target:{value:'{"case":"ok"}'}});
 const testInput=screen.getByLabelText('契约测试参数（JSON）');
 fireEvent.click(screen.getByRole('button',{name:'执行测试'}));
 await screen.findByText('当前配置已通过执行测试');
 expect(route).toHaveBeenLastCalledWith('function','new-function',2);
 expect(screen.getByRole('button',{name:/3 验证与发布/})).toHaveAttribute('aria-current','step');
 fireEvent.click(screen.getByRole('button',{name:'展开工作页'}));
 expect(screen.getByRole('region',{name:'新建功能'})).toBeVisible();
 expect(screen.getByLabelText('契约测试参数（JSON）')).toBe(testInput);expect(testInput).toHaveValue('{"case":"ok"}');
 expect(screen.getByText('当前配置已通过执行测试')).toBeVisible();
 expect(post.mock.calls.filter(([p])=>p.endsWith('/import'))).toHaveLength(1);
 expect(post.mock.calls.filter(([p])=>p.endsWith('/validate'))).toHaveLength(1);
});
it('restores only validation matching the draft revision and does not probe on mount',async()=>{
 const api=fixture(),post=vi.spyOn(api,'post');
 const current={...draft,validation:{valid:true,revision:1,errors:[],checks:{},probe:null,execution_test:{passed:true,revision:1,status:"SUCCEEDED"}}};
 const view=render(<FunctionEditor api={api} workspace="ws" item={current} writable initialStep={2} onClose={vi.fn()} onSaved={async()=>{}}/>);
 expect(screen.getByText('当前配置已通过执行测试')).toBeVisible();
 await waitFor(()=>expect(screen.getByRole('button',{name:'发布功能版本'})).toBeEnabled());expect(post).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText('功能名称'),{target:{value:'变更'}});expect(screen.getByRole('button',{name:'发布功能版本'})).toBeDisabled();
 view.unmount();render(<FunctionEditor api={api} workspace="ws" item={{...current,revision:2}} writable initialStep={2} onClose={vi.fn()} onSaved={async()=>{}}/>);
 expect(screen.queryByText('当前配置已通过执行测试')).toBeNull();expect(post).not.toHaveBeenCalled();
});
it('keeps a successfully saved model when testing fails and retries without creating a version',async()=>{
 const api=fixture();const model={id:'m',workspace_id:'ws',name:'测试配置',revision:1,latest_version:1,disabled:false,latest:{id:'mv',profile_id:'m',version:1,has_api_key:true,created_at:'',config:{protocol:'openai-compatible' as const,endpoint:'https://example.com/chat/completions',model:'test',model_version:'v1',thinking_mode:'default' as const,timeout_seconds:30}}};
 let testCount=0;const post=vi.spyOn(api,'post').mockImplementation(async p=>{if(p.endsWith('/test')){if(++testCount===1)throw new Error('测试连接失败');return {ok:true,profile_id:'m',version:1,elapsed_ms:20} as never;}return model as never;});
 render(<StrictMode><ModelEditor api={api} workspace="ws"/></StrictMode>);
 for(const [label,value] of [['配置名称','测试配置'],['完整请求地址',model.latest.config.endpoint],['模型名','test'],['模型版本','v1'],['访问凭据','synthetic-only']])fireEvent.change(screen.getByLabelText(label),{target:{value}});
 fireEvent.submit(screen.getByRole('button',{name:'保存并测试'}).closest('form')!);
 await screen.findByText(/测试连接失败/);expect(screen.getByLabelText('配置名称')).toHaveValue('测试配置');expect(screen.getByLabelText('访问凭据')).toHaveValue('');
 fireEvent.submit(screen.getByRole('button',{name:'测试连接及结构化输出'}).closest('form')!);
 await screen.findByText(/连接与结构化输出通过/);
 expect(post.mock.calls.filter(([p])=>p.endsWith('/model-profiles'))).toHaveLength(1);expect(post.mock.calls.filter(([p])=>p.endsWith('/versions'))).toHaveLength(0);expect(testCount).toBe(2);
});
it('empty creation closes without a warning; a changed form requires explicit discard',async()=>{
 const api=fixture(),close=vi.fn();render(<CreationFlow api={api} workspace="ws" session={{key:1,kind:'function',origin:'/spaces/ws/functions',expanded:false}} functions={[]} admin={false} publishable onStay={vi.fn()} onClose={close} onAgent={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} onExpand={vi.fn()} onRoute={vi.fn()} onSaved={async()=>{}}/>);
 fireEvent.click(screen.getByRole('button',{name:'关闭编辑'}));expect(close).toHaveBeenCalledOnce();
 fireEvent.change(screen.getByLabelText('功能名称'),{target:{value:'本地未保存'}});fireEvent.click(screen.getByRole('button',{name:'关闭编辑'}));expect(screen.getByText('保留当前修改？')).toBeVisible();expect(close).toHaveBeenCalledOnce();
 fireEvent.click(screen.getByRole('button',{name:'继续编辑'}));expect(screen.getByLabelText('功能名称')).toHaveValue('本地未保存');
});
