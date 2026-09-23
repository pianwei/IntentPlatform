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
/** Small controlled Markdown profile. HTML and images remain text; no HTML interpreter. */
export function SafeMarkdown({ text }: { text: string }) {
  const blocks = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  return <div className="safe-markdown">{blocks.map((block, i) => block.startsWith("```") ? <pre key={i}><code>{block.replace(/^```[^\n]*\n/, "").replace(/```$/, "")}</code></pre> : <div key={i}>{block.split("\n").map((line, n) => {
    if (/^#{1,3}\s/.test(line)) return <h4 key={n}>{inline(line.replace(/^#{1,3}\s+/, ""))}</h4>;
    if (/^[-*]\s/.test(line)) return <div className="markdown-list-item" key={n}>• {inline(line.slice(2))}</div>;
    return line ? <p key={n}>{inline(line)}</p> : <br key={n} />;
  })}</div>)}</div>;
}
