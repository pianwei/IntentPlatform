import { ArrowUp, MessageSquare } from "lucide-react";
import type { RuntimeUI } from "./types";

export function ApplicationIcon({ ui, assetUrl }: { ui: RuntimeUI; assetUrl?: string }) {
  return <span className="application-icon" aria-hidden="true">
    {ui.icon.kind === "asset" && assetUrl ? <img src={assetUrl} alt="" /> : ui.icon.kind === "emoji" ? ui.icon.value : "✦"}
  </span>;
}
export function Welcome({ ui, assetUrl, onSelect, disabled = false }: {
  ui: RuntimeUI; assetUrl?: string; onSelect?: (text: string) => void; disabled?: boolean;
}) {
  return <div className="welcome application-welcome">
    <ApplicationIcon ui={ui} assetUrl={assetUrl} />
    <h2>{ui.welcome_message}</h2>
    <p>{ui.description}</p>
    <div className="suggestions">
      {ui.suggested_questions.map((text, index) => <button key={`${text}-${index}`} disabled={disabled || !onSelect} onClick={() => onSelect?.(text)}>
        <MessageSquare size={16} /><span>{text}</span><ArrowUp size={15} />
      </button>)}
    </div>
  </div>;
}
export function ApplicationPreview({ ui, assetUrl, embedded = false }: { ui: RuntimeUI; assetUrl?: string; embedded?: boolean }) {
  return <section className="application-preview" aria-label="草稿视觉预览" style={{ "--app-accent": ui.primary_color } as React.CSSProperties}>
    <header><ApplicationIcon ui={ui} assetUrl={assetUrl} /><strong>{ui.title}</strong><span>视觉预览</span></header>
    <div className="preview-body">
      {!embedded && ui.show_history && <aside className="preview-history"><strong>开始新会话</strong><p>历史会话</p><small>实际会话将在使用后显示</small></aside>}
      <div className="preview-thread"><Welcome ui={ui} assetUrl={assetUrl} disabled />
        <div className="preview-composer">{ui.input_placeholder}<ArrowUp size={18} /></div>
        <p className="preview-note">仅预览界面，不创建会话或执行业务。</p>
      </div>
    </div>
  </section>;
}
