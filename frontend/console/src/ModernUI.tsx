import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Popover from "@radix-ui/react-popover";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Check, ChevronDown, Info, Search, X } from "lucide-react";
const FloatingContainer=createContext<HTMLElement|undefined>(undefined);
export function Help({ text }: { text: string }) {
 const container=useContext(FloatingContainer);
 return <Popover.Root><Popover.Trigger asChild><button type="button" className="icon-button help" aria-label="查看说明"><Info size={15}/></button></Popover.Trigger><Popover.Portal container={container}><Popover.Content className="ui-popover help-content" sideOffset={8}>{text}<Popover.Arrow className="popover-arrow"/></Popover.Content></Popover.Portal></Popover.Root>;
}
export function Hint({text,children}:{text:string;children:ReactNode}){const container=useContext(FloatingContainer);return <Tooltip.Provider delayDuration={450}><Tooltip.Root><Tooltip.Trigger asChild>{children}</Tooltip.Trigger><Tooltip.Portal container={container}><Tooltip.Content className="ui-tooltip" sideOffset={6}>{text}</Tooltip.Content></Tooltip.Portal></Tooltip.Root></Tooltip.Provider>;}
export function Status({ value }: { value: string }) {
 const tone = ["已完成", "已发布", "通过", "有效", "正常"].includes(value) ? "success" : ["失败", "已撤销", "已停用"].includes(value) ? "danger" : ["待补充", "草稿", "待确认"].includes(value) ? "warning" : "neutral";
 return <span className={`status ${tone}`}><i />{value}</span>;
}
export function Modal({ title, children, onClose, wide = false, busy = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean; busy?: boolean }) {
 const container=useContext(FloatingContainer);
 const previous=useRef(document.activeElement as HTMLElement|null);
 return <Dialog.Root open onOpenChange={open=>{if(!open&&!busy)onClose();}}><Dialog.Portal container={container}><Dialog.Overlay className="modal-overlay"/><Dialog.Content className={`modal ${wide?"wide":""}`} aria-describedby={undefined} onEscapeKeyDown={e=>{if(busy)e.preventDefault();}} onCloseAutoFocus={e=>{e.preventDefault();previous.current?.focus();}} onPointerDownOutside={e=>e.preventDefault()}>
 <header><Dialog.Title asChild><h2>{title}</h2></Dialog.Title><Dialog.Close asChild><button type="button" disabled={busy} className="icon-button" aria-label="关闭弹窗"><X size={19}/></button></Dialog.Close></header>{children}
 </Dialog.Content></Dialog.Portal></Dialog.Root>;
}

/** The same DOM/editor remains mounted when moving between a top-layer dialog and a resource page. */
export function TaskSurface({title,children,expanded,onExpand,onClose,wide=false,busy=false}:{title:string;children:ReactNode;expanded:boolean;onExpand:()=>void;onClose:()=>void;wide?:boolean;busy?:boolean}){
 const [container,setContainer]=useState<HTMLDialogElement|null>(null);
 const node=useRef<HTMLDialogElement|null>(null),previous=useRef(document.activeElement as HTMLElement|null);
 const attach=useCallback((el:HTMLDialogElement|null)=>{node.current=el;setContainer(el);},[]);
 useEffect(()=>{const el=node.current;if(!el)return;if(expanded){if(el.open)el.close?.();el.setAttribute('open','');}else{el.removeAttribute('open');if(el.showModal)el.showModal();else el.setAttribute('open','');el.querySelector<HTMLElement>('input:not([disabled]):not([type=checkbox]),textarea:not([disabled])')?.focus();}},[expanded]);
 useEffect(()=>()=>{previous.current?.focus();},[]);
 return <dialog ref={attach} className={`task-surface ${expanded?'task-page':'task-dialog'} ${wide?'task-wide':''}`} role={expanded?"region":"dialog"} aria-label={title} aria-modal={expanded?undefined:true} onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
 <header className="task-heading"><div><span className="task-context">{expanded?'工作页':'快速创建'}</span><h2>{title}</h2></div><div className="task-heading-actions">{!expanded&&<button className="text-button" disabled={busy} onClick={onExpand}>展开工作页</button>}<button className="icon-button" disabled={busy} aria-label="关闭编辑" onClick={onClose}><X size={19}/></button></div></header><div className="task-content"><FloatingContainer.Provider value={container??undefined}>{children}</FloatingContainer.Provider></div></dialog>;
}
export function PageTitle({ title, help, children }: { title: string; help?: string; children?: ReactNode }) {return <div className="page-title"><div><h1>{title}</h1>{help&&<Help text={help}/>}</div><div className="page-actions">{children}</div></div>;}
export function Anchored({trigger,children,align="start"}:{trigger:ReactNode;children:ReactNode;align?:"start"|"end"}){const container=useContext(FloatingContainer);return <Popover.Root><Popover.Trigger asChild>{trigger}</Popover.Trigger><Popover.Portal container={container}><Popover.Content className="ui-popover" align={align} sideOffset={6} collisionPadding={12}>{children}</Popover.Content></Popover.Portal></Popover.Root>;}
export function SearchSelect({label,value,options,onChange,placeholder="请选择",disabled=false,footer}:{label:string;value:string;options:{value:string;label:string;detail?:string}[];onChange:(v:string)=>void;placeholder?:string;disabled?:boolean;footer?:ReactNode}){
 const container=useContext(FloatingContainer);
 const [open,setOpen]=useState(false),[query,setQuery]=useState(""); const list=options.filter(o=>`${o.label} ${o.detail??""}`.toLowerCase().includes(query.toLowerCase()));
 return <Popover.Root open={open} onOpenChange={v=>{setOpen(v);if(v)setQuery("");}}><Popover.Trigger asChild><button type="button" disabled={disabled} className="select-trigger" aria-label={label}><span>{options.find(o=>o.value===value)?.label??placeholder}</span><ChevronDown size={14}/></button></Popover.Trigger><Popover.Portal container={container}><Popover.Content className="ui-popover selector-popover" sideOffset={6} align="start" collisionPadding={12}>
 <label className="popover-search"><Search size={15}/><input aria-label={`搜索${label}`} placeholder="搜索…" value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='ArrowDown'){e.preventDefault();e.currentTarget.parentElement?.nextElementSibling?.querySelector<HTMLButtonElement>('button')?.focus();}}}/></label>
 <div className="select-options" role="listbox" aria-label={label}>{list.map((o,i)=><button type="button" role="option" aria-selected={o.value===value} key={o.value} onClick={()=>{onChange(o.value);setOpen(false);}} onKeyDown={e=>{const buttons=e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('button');if(!buttons)return;if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();buttons[(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}}}><span><strong>{o.label}</strong>{o.detail&&<small>{o.detail}</small>}</span>{value===o.value&&<Check size={15}/>}</button>)}{!list.length&&<p className="menu-empty">没有匹配项</p>}</div>{footer&&<div className="popover-footer" onClick={()=>setOpen(false)}>{footer}</div>}
 </Popover.Content></Popover.Portal></Popover.Root>;
}
export type MenuAction={label:string;onSelect:()=>void;icon?:ReactNode;danger?:boolean;disabled?:boolean};
export function ActionMenu({trigger,items}:{trigger:ReactNode;items:MenuAction[]}){const container=useContext(FloatingContainer);return <Dropdown.Root><Dropdown.Trigger asChild>{trigger}</Dropdown.Trigger><Dropdown.Portal container={container}><Dropdown.Content className="ui-menu" sideOffset={6} align="end" collisionPadding={12}>{items.map((item,i)=><Dropdown.Item key={i} className={item.danger?"danger-item":""} disabled={item.disabled} onSelect={item.onSelect}>{item.icon}{item.label}</Dropdown.Item>)}</Dropdown.Content></Dropdown.Portal></Dropdown.Root>;}
