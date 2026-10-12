import { SafeMarkdown } from "./SafeMarkdown";
import { useRef, useState, type CSSProperties } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Maximize2, X } from "lucide-react";
export function valueText(value: unknown) {
  return value === null || value === undefined
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
const businessLabels: Record<string, string> = {
  customers: "客户", customer_id: "客户编号", name: "名称", city: "城市", masked_id: "证件标识",
  accounts: "账户", account_id: "账户编号", label: "账户名称", currency: "币种",
  balance: "余额", balances: "账户余额", available: "可用余额", status: "状态", reason: "原因",
  transactions: "交易", transaction_id: "交易编号", posted_on: "交易日期",
  direction: "收支方向", amount: "金额", description: "说明", start_date: "开始日期", end_date: "结束日期",
  income: "收入", expense: "支出", net: "净额", count: "笔数",
  loans: "贷款合同", loan_id: "合同编号", product_name: "产品名称", outstanding: "剩余余额",
  payments: "还款安排", schedules: "还款安排", schedule: "还款计划", due_on: "到期日", overdue: "逾期记录", days: "逾期天数",
  products: "产品", product_id: "产品编号", category: "类别", holdings: "持仓",
  holding_id: "持仓编号", units: "持有份额", tickets: "工单", ticket_id: "工单编号",
  history: "处理记录", risk_level: "风险等级", as_of: "登记日期", segment: "客户分类",
  summary: "汇总", detail: "详情", details: "明细", total_balance: "总余额", total_available: "总可用余额",
};
export function businessLabel(name: string) { return businessLabels[name] ?? name; }
const resultViewLimits = { depth: 3, rows: 50, columns: 20, fields: 50 };
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function BusinessTable({ rows, depth }: { rows: Record<string, unknown>[]; depth: number }) {
  const host = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState("1");
  const visibleRows = rows.slice(0, resultViewLimits.rows);
  const allColumns = Array.from(new Set(visibleRows.flatMap(row => Object.keys(row))));
  const columns = allColumns.slice(0, resultViewLimits.columns);
  if (!rows.length) return <p className="muted">暂无结果记录。</p>;
  const table = <div className="table-scroll">
    <table>
      <thead><tr>{columns.map(column => <th key={column}>{businessLabel(column)}</th>)}</tr></thead>
      <tbody>{visibleRows.map((row, index) => <tr key={index}>{columns.map(column =>
        <td key={column}><BusinessValue value={row[column]} depth={depth + 1} /></td>,
      )}</tr>)}</tbody>
    </table>
    {rows.length > resultViewLimits.rows && <p className="muted small">共 {rows.length} 条记录，仅展示前 {resultViewLimits.rows} 条。</p>}
    {allColumns.length > resultViewLimits.columns && <p className="muted small">字段较多，仅展示前 {resultViewLimits.columns} 列。</p>}
  </div>;
  return <div className="business-table" ref={host}>
    {(rows.length > 4 || columns.length > 3) && <div className="business-table-tools"><span>{rows.length} 条记录</span>
      <Dialog.Root onOpenChange={open => { if (open && host.current) setScale(getComputedStyle(host.current).getPropertyValue("--text-scale").trim() || "1"); }}><Dialog.Trigger className="business-table-expand" type="button"><Maximize2 size={14} />放大表格</Dialog.Trigger>
        <Dialog.Portal><Dialog.Overlay className="business-table-overlay" /><Dialog.Content className="business-table-dialog" style={{ "--text-scale": scale } as CSSProperties}>
          <div className="business-table-dialog-heading"><div><Dialog.Title>结果明细</Dialog.Title><Dialog.Description>{rows.length} 条已返回记录</Dialog.Description></div>
            <Dialog.Close className="business-table-expand" aria-label="关闭表格"><X size={18} /></Dialog.Close></div>{table}
        </Dialog.Content></Dialog.Portal>
      </Dialog.Root>
    </div>}{table}
  </div>;
}
function BusinessFields({ fields, depth = 0 }: { fields: { label: string; value: unknown }[]; depth?: number }) {
  return <>
    <dl className="detail-grid">
      {fields.slice(0, resultViewLimits.fields).map((field, index) => (
        <div key={`${field.label}-${index}`} className={field.value !== null && typeof field.value === "object" ? "detail-value-complex" : undefined}>
          <dt>{businessLabel(field.label)}</dt>
          <dd><BusinessValue value={field.value} depth={depth + 1} /></dd>
        </div>
      ))}
    </dl>
    {fields.length > resultViewLimits.fields && <p className="muted small">字段较多，仅展示前 {resultViewLimits.fields} 项。</p>}
  </>;
}
function BusinessValue({ value, depth }: { value: unknown; depth: number }) {
  if (value === null || typeof value !== "object") return <>{valueText(value)}</>;
  if (depth >= resultViewLimits.depth) return <span className="muted small">内容层级较深，未展开。</span>;
  if (Array.isArray(value)) {
    if (value.every(isRecord)) return <BusinessTable rows={value} depth={depth} />;
    return <>
      <ul className="business-value-list">{value.slice(0, resultViewLimits.rows).map((item, index) =>
        <li key={index}><BusinessValue value={item} depth={depth + 1} /></li>,
      )}</ul>
      {value.length > resultViewLimits.rows && <p className="muted small">共 {value.length} 条记录，仅展示前 {resultViewLimits.rows} 条。</p>}
    </>;
  }
  const fields = Object.entries(value).map(([label, fieldValue]) => ({ label, value: fieldValue }));
  return fields.length ? <BusinessFields fields={fields} depth={depth} /> : <span className="muted">暂无详情。</span>;
}
export function BusinessResultContent({ data }: { data: Record<string, unknown> }) {
  const result = data.data;
  const component = String(data.component ?? "detail");
  const resultObject = result && typeof result === "object" && !Array.isArray(result)
    ? result as Record<string, unknown> : null;
  const namedRows = resultObject && Object.entries(resultObject).find(([, value]) =>
    Array.isArray(value) && value.every(item => item !== null && typeof item === "object" && !Array.isArray(item)));
  const rows = Array.isArray(result)
    ? result
    : resultObject && Array.isArray(resultObject.rows)
      ? resultObject.rows
      : namedRows
      ? namedRows[1] as unknown[]
      : null;
  const fields = Array.isArray(data.fields)
    ? (data.fields as { label: string; value: unknown }[])
    : result && typeof result === "object" && !Array.isArray(result)
      ? Object.entries(result).map(([label, value]) => ({ label, value }))
      : [{ label: "结果", value: result }];
  const tableRows = rows?.filter(
    (row): row is Record<string, unknown> =>
      row !== null && typeof row === "object" && !Array.isArray(row),
  );
  return <>
      {data.result_unavailable === true ? (
        <p className="result-text" role="status">业务操作已确认成功，但返回内容未通过已发布的展示契约。请按调用记录核对结果；此处不会重新提交业务。</p>
      ) : component === "table" && tableRows ? (
        <BusinessTable rows={tableRows} depth={0} />
      ) : component === "file" ? null : component === "markdown" ? (
        <SafeMarkdown text={valueText(result)} />
      ) : component === "citation" ? (
        <div className="citation-result"><SafeMarkdown text={typeof result === "string" ? result : valueText(result)} /><p className="muted small">仅展示服务端准许的引用内容；文件通过授权下载读取。</p></div>
      ) : component === "text" ? (
        <SafeMarkdown text={valueText(result)} />
      ) : (
        <BusinessFields fields={fields} />
      )}
  </>;
}
