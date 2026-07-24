import type React from 'react';

function renderInlineMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    nodes.push(token.startsWith('`')
      ? <code key={nodes.length}>{token.slice(1, -1)}</code>
      : <strong key={nodes.length}>{token.slice(2, -2)}</strong>);
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function renderMarkdownLite(markdown: string): React.ReactNode[] {
  const lines = markdown.split(/\r?\n/);
  const nodes: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    nodes.push(<p key={`p-${nodes.length}`}>{renderInlineMarkdown(paragraph.join(' '))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const Tag = list.ordered ? 'ol' : 'ul';
    nodes.push(
      <Tag key={`list-${nodes.length}`}>
        {list.items.map((item, index) => <li key={index}>{renderInlineMarkdown(item)}</li>)}
      </Tag>,
    );
    list = null;
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1].length + 1, 4);
      const content = renderInlineMarkdown(heading[2]);
      if (level === 2) nodes.push(<h2 key={`h-${nodes.length}`}>{content}</h2>);
      else if (level === 3) nodes.push(<h3 key={`h-${nodes.length}`}>{content}</h3>);
      else nodes.push(<h4 key={`h-${nodes.length}`}>{content}</h4>);
      return;
    }
    const ordered = /^\d+\.\s+(.+)$/.exec(trimmed);
    const unordered = /^[-*]\s+(.+)$/.exec(trimmed);
    if (ordered || unordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = { ordered: isOrdered, items: [] };
      }
      list.items.push((ordered || unordered)?.[1] || '');
      return;
    }
    flushList();
    paragraph.push(trimmed);
  });
  flushParagraph();
  flushList();
  return nodes;
}
