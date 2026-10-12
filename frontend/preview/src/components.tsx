import { useEffect, useId, useRef, type ReactNode } from "react";
import { Info, X } from "lucide-react";

export function Help({ text }: { text: string }) {
  return <details className="help"><summary aria-label="查看说明"><Info size={15} /></summary><div>{text}</div></details>;
}
export function Status({ value }: { value: string }) {
  const tone = ["已完成", "已发布", "通过", "有效"].includes(value) ? "success" : ["失败", "已撤销"].includes(value) ? "danger" : ["待补充", "草稿", "待确认"].includes(value) ? "warning" : "neutral";
  return <span className={`status ${tone}`}><i />{value}</span>;
}
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const label = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    if (node && !node.open) node.showModal();
    return () => { node?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={ref} aria-labelledby={label} className={`modal ${wide ? "wide" : ""}`} onCancel={e => { e.preventDefault(); closeRef.current(); }}>
    <header><h2 id={label}>{title}</h2><button className="icon-button" aria-label="关闭弹窗" onClick={onClose}><X size={19} /></button></header>{children}
  </dialog>;
}
export function PageTitle({ title, help, children }: { title: string; help?: string; children?: ReactNode }) {
  return <div className="page-title"><div><h1>{title}</h1>{help && <Help text={help} />}</div>{children}</div>;
}
