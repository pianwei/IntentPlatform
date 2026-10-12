import { Fragment, type ReactNode } from "react";

export function safeLink(raw: string): string | null {
  if (/[\u0000-\u0020\u007f]/.test(raw)) return null;
  try {
    const url = new URL(raw, location.origin);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  } catch { return null; }
}
function inline(value: string): ReactNode[] {
  const result: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\s)]+\))/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    result.push(value.slice(cursor, match.index));
    const token = match[0], key = match.index;
    if (token.startsWith("`")) result.push(<code key={key}>{token.slice(1, -1)}</code>);
    else if (token.startsWith("**")) result.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    else {
      const parts = token.match(/^\[([^\]]+)\]\((.+)\)$/)!;
      const href = safeLink(parts[2]);
      result.push(href ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{parts[1]}</a> : <Fragment key={key}>{parts[1]}</Fragment>);
    }
    cursor = match.index! + token.length;
  }
  result.push(value.slice(cursor));
  return result;
}
function tableCells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells: string[] = []; let current = "", code = false;
  for (let i = 0; i < trimmed.length; i++) {
    const char = trimmed[i];
    if (char === "\\" && trimmed[i + 1] === "|") { current += "|"; i++; }
    else if (char === "`") { code = !code; current += char; }
    else if (char === "|" && !code) { cells.push(current.trim()); current = ""; }
    else current += char;
  }
  cells.push(current.trim()); return cells;
}
function linesContent(block: string): ReactNode[] {
  const lines = block.split("\n"), result: ReactNode[] = [];
  for (let n = 0; n < lines.length; n++) {
    const line = lines[n];
    if (line.includes("|") && n + 1 < lines.length && tableCells(lines[n + 1]).every(cell => /^:?-{3,}:?$/.test(cell))) {
      const columns = tableCells(line).slice(0, 20), rows: string[][] = []; const key = n; n++;
      while (n + 1 < lines.length && lines[n + 1].trim() && lines[n + 1].includes("|") && rows.length < 100) rows.push(tableCells(lines[++n]));
      result.push(<div className="markdown-table-scroll" key={key}><table><thead><tr>{columns.map((cell, i) => <th key={i}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i}>{columns.map((_, col) => <td key={col}>{inline(row[col] ?? "")}</td>)}</tr>)}</tbody></table></div>);
    } else if (/^#{1,6}\s/.test(line)) result.push(<h4 key={n}>{inline(line.replace(/^#{1,6}\s+/, ""))}</h4>);
    else if (/^[-*]\s/.test(line)) result.push(<div className="markdown-list-item" key={n}>• {inline(line.slice(2))}</div>);
    else if (/^\d+[.)]\s/.test(line)) result.push(<div className="markdown-list-item" key={n}>{inline(line)}</div>);
    else if (/^>\s?/.test(line)) result.push(<blockquote key={n}>{inline(line.replace(/^>\s?/, ""))}</blockquote>);
    else result.push(line ? <p key={n}>{inline(line)}</p> : <br key={n} />);
  }
  return result;
}
/** Finite Markdown; all content is rendered through React text, never as HTML. */
export function SafeMarkdown({ text }: { text: string }) {
  const blocks = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  return <div className="safe-markdown">{blocks.map((block, i) => block.startsWith("```") ? <pre key={i}><code>{block.replace(/^```[^\n]*\n/, "").replace(/```$/, "")}</code></pre> : <div key={i}>{linesContent(block)}</div>)}</div>;
}
