import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowUpRight, Check, ChevronDown, Clock3, GitBranch, Layers3, ListFilter, MessageSquare, RefreshCw, Search, UserRound } from "lucide-react";
import { conversations, type Conversation } from "./data";
import { Help, PageTitle, Status } from "./components";

export function Logs({ notice }: { notice: (text: string) => void }) {
  const [query, setQuery] = useState("");
  const [agent, setAgent] = useState("");
  const [status, setStatus] = useState("");
  const [date, setDate] = useState("");
  const [selected, setSelected] = useState(1);
  const [turn, setTurn] = useState(1);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [traceOnly, setTraceOnly] = useState(false);
  const filtered = useMemo(() => conversations.filter(c => (!query || c.title.includes(query) || c.turns.some(t => t.question.includes(query))) && (!agent || c.agent === agent) && (!status || c.turns.at(-1)?.state === status) && (!date || c.date === date)), [query, agent, status, date]);
  const item = filtered.find(c => c.key === selected) ?? filtered[0];
  const selectedTurn = item?.turns[Math.min(turn, item.turns.length - 1)];
  useEffect(() => { if (!filtered.length) setMobileDetail(false); }, [filtered.length]);
  function open(c: Conversation) { setSelected(c.key); setTurn(c.turns.length - 1); setMobileDetail(true); setTraceOnly(false); }
  return <section className="log-page">
    <PageTitle title="运行日志" help="以一次会话查看完整问答，再展开每一轮的实体、动力、策划与执行记录。">
      <button className="button" onClick={() => notice("当前筛选下的预览记录已刷新")}><RefreshCw size={15} />刷新</button>
    </PageTitle>
    <div className="log-filters">
      <label className="search-box"><Search size={16} /><input aria-label="搜索会话" placeholder="搜索会话内容…" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <select aria-label="筛选智能体" value={agent} onChange={e => setAgent(e.target.value)}><option value="">全部智能体</option><option>综合业务助手</option><option>客户经理助手</option></select>
      <select aria-label="筛选状态" value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option><option>已完成</option><option>待补充</option><option>失败</option></select>
      <select aria-label="筛选时间" value={date} onChange={e => setDate(e.target.value)}><option value="">最近两天</option><option>今天</option><option>昨天</option></select>
      <span className="result-count"><ListFilter size={14} />{filtered.length} 个会话</span>
    </div>
    <div className={`log-workbench ${mobileDetail && item && selectedTurn ? "show-detail" : ""}`}>
      <aside className="conversation-list" aria-label="会话列表">
        <div className="list-heading">会话<span>最近活动</span></div>
        {!filtered.length && <div className="empty-state"><Search size={28} /><h3>没有匹配的会话</h3><p>试试其他关键词或筛选条件</p><button className="button" onClick={() => { setQuery(""); setAgent(""); setStatus(""); setDate(""); }}>清除筛选</button></div>}
        {filtered.map(c => <button key={c.key} className={`conversation-item ${c.key === item?.key ? "selected" : ""}`} onClick={() => open(c)} aria-current={c.key === item?.key ? "true" : undefined}>
          <div className="conversation-item-top"><MessageSquare size={15} /><strong>{c.title}</strong><time>{c.time}</time></div>
          <p>{c.turns.at(-1)?.question}</p>
          <div className="conversation-item-bottom"><span>{c.agent} · {c.turns.length} 轮</span><Status value={c.turns.at(-1)!.state} /></div>
        </button>)}
        <div className="list-footer">已显示全部 {filtered.length} 个会话</div>
      </aside>
      {item && selectedTurn ? <article className="conversation-detail" aria-label="会话详情">
        <header className="detail-header"><button className="icon-button mobile-only" aria-label="返回会话列表" onClick={() => setMobileDetail(false)}><ArrowLeft size={18} /></button><div><h2>{item.title}</h2><div className="detail-meta"><span><Layers3 size={13} />{item.agent}</span><span><UserRound size={13} />{item.actor}</span><span>{item.date} {item.time}</span></div></div></header>
        <div className="detail-tabs-row"><div className="turn-tabs" role="tablist" aria-label="问答轮次">{item.turns.map((t, i) => <button key={i} role="tab" aria-selected={selectedTurn === t} className={selectedTurn === t ? "active" : ""} onClick={() => { setTurn(i); setTraceOnly(false); }}>第 {i + 1} 轮{item.turns.length > 1 && i === item.turns.length - 1 && <span>最近</span>}</button>)}</div><button className="trace-shortcut" aria-pressed={traceOnly} onClick={() => setTraceOnly(!traceOnly)}>{traceOnly ? <MessageSquare size={13} /> : <GitBranch size={13} />}{traceOnly ? "返回问答" : "查看处理过程"}</button></div>
        <div className="detail-scroll" key={`${item.key}-${turn}-${traceOnly}`}>
          <div className="turn-status"><Status value={selectedTurn.state} /><span><Clock3 size={14} />处理 {selectedTurn.elapsed}</span><span>等待用户 {selectedTurn.wait}</span></div>
          {!traceOnly && <><div className="message-block"><div className="message-label"><span className="message-icon"><UserRound size={15} /></span>用户输入</div><p className="question-text">{selectedTurn.question}</p></div>
          <div className="message-block answer-block"><div className="message-label"><span className="message-icon assistant"><Layers3 size={15} /></span>最终答复</div><p>{selectedTurn.answer}</p>
            {selectedTurn.balances && <div className="balance-table"><table><thead><tr><th>账户</th><th>类型</th><th>币种</th><th className="numeric">余额</th></tr></thead><tbody>{[["•••• 2608", "活期账户", "28,800.00"], ["•••• 7182", "活期账户", "156,200.00"], ["•••• 9036", "定期账户", "50,000.00"]].map(row => <tr key={row[0]}><td>{row[0]}</td><td>{row[1]}</td><td>人民币</td><td className="numeric">{row[2]}</td></tr>)}</tbody><tfoot><tr><td colSpan={3}>合计</td><td className="numeric">235,000.00</td></tr></tfoot></table></div>}
          </div></>}
          <div className="trace-heading"><h3><GitBranch size={16} />处理过程</h3><Help text="这里展示实际记录的决定与执行事实。没有采集的阶段耗时不作推算。" /></div>
          <div className="phase-list">{selectedTurn.phases.map((phase, i) => <details className={`phase ${phase.state === "失败" ? "failed" : ""}`} key={phase.name} open={phase.state === "失败" || undefined}>
            <summary><span className="phase-number">{phase.state === "失败" ? "!" : <Check size={12} />}</span><span><strong>{phase.name}</strong><small>{phase.summary}</small></span><ChevronDown size={16} /></summary>
            <div className="phase-facts"><dl>{phase.facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div>
          </details>)}</div>
          <div className="trace-end"><Check size={13} />以上为本轮已记录内容</div>
        </div>
      </article> : <div className="empty-detail"><MessageSquare size={34} /><p>选择会话查看问答与处理过程</p></div>}
    </div>
  </section>;
}
