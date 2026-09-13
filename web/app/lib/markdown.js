const SAFE_LINK_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);

function safeHref(value) {
  try {
    const url = new URL(value, window.location.href);
    return SAFE_LINK_PROTOCOLS.has(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function appendInline(parent, source) {
  const pattern = /(`[^`\n]+`|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|~~([^~\n]+)~~|\*([^*\n]+)\*|_([^_\n]+)_)/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(source))) {
    if (match.index > cursor) parent.append(document.createTextNode(source.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith('`')) {
      const code = document.createElement('code');
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else if (token.startsWith('[')) {
      const href = safeHref(match[3]);
      if (href) {
        const link = document.createElement('a');
        link.href = href;
        link.textContent = match[2];
        if (link.protocol === 'http:' || link.protocol === 'https:') {
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
        }
        parent.append(link);
      } else {
        parent.append(document.createTextNode(match[2]));
      }
    } else {
      const element = document.createElement(token.startsWith('**') || token.startsWith('__') ? 'strong' : token.startsWith('~~') ? 'del' : 'em');
      element.textContent = match[4] || match[5] || match[6] || match[7] || match[8] || '';
      parent.append(element);
    }
    cursor = pattern.lastIndex;
  }
  if (cursor < source.length) parent.append(document.createTextNode(source.slice(cursor)));
}

function splitTableRow(line) {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
}

function isTableDivider(line = '') {
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isBlockStart(lines, index) {
  const line = lines[index] || '';
  return /^\s*```/.test(line) || /^#{1,4}\s+/.test(line) || /^\s*([-*+]\s+|\d+\.\s+|>\s?)/.test(line) || /^\s*(?:-{3,}|\*{3,})\s*$/.test(line) || (line.includes('|') && isTableDivider(lines[index + 1]));
}

function appendTable(container, lines, start) {
  const headers = splitTableRow(lines[start]);
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  headers.forEach((value) => {
    const cell = document.createElement('th');
    appendInline(cell, value);
    headerRow.append(cell);
  });
  thead.append(headerRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  let index = start + 2;
  while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
    const row = document.createElement('tr');
    splitTableRow(lines[index]).forEach((value) => {
      const cell = document.createElement('td');
      appendInline(cell, value);
      row.append(cell);
    });
    tbody.append(row);
    index += 1;
  }
  table.append(tbody);
  const wrapper = document.createElement('div');
  wrapper.className = 'markdown-table-wrap';
  wrapper.append(table);
  container.append(wrapper);
  return index;
}

export function renderMarkdown(container, markdown = '') {
  container.replaceChildren();
  const lines = String(markdown).replace(/\r\n?/g, '\n').split('\n');
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = line.match(/^\s*```([\w-]*)\s*$/);
    if (fence) {
      const chunks = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) chunks.push(lines[index++]);
      if (index < lines.length) index += 1;
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      if (fence[1]) code.dataset.language = fence[1];
      code.textContent = chunks.join('\n');
      pre.append(code);
      container.append(pre);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const element = document.createElement(`h${Math.min(4, heading[1].length + 2)}`);
      appendInline(element, heading[2]);
      container.append(element);
      index += 1;
      continue;
    }
    if (line.includes('|') && isTableDivider(lines[index + 1])) {
      index = appendTable(container, lines, index);
      continue;
    }
    if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) {
      container.append(document.createElement('hr'));
      index += 1;
      continue;
    }
    const listMatch = line.match(/^\s*([-*+]\s+|\d+\.\s+)(.+)$/);
    if (listMatch) {
      const ordered = /^\d/.test(listMatch[1]);
      const list = document.createElement(ordered ? 'ol' : 'ul');
      const matcher = ordered ? /^\s*\d+\.\s+(.+)$/ : /^\s*[-*+]\s+(.+)$/;
      while (index < lines.length) {
        const itemMatch = lines[index].match(matcher);
        if (!itemMatch) break;
        const item = document.createElement('li');
        appendInline(item, itemMatch[1]);
        list.append(item);
        index += 1;
      }
      container.append(list);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote = document.createElement('blockquote');
      const parts = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) parts.push(lines[index++].replace(/^\s*>\s?/, ''));
      appendInline(quote, parts.join(' '));
      container.append(quote);
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) paragraph.push(lines[index++].trim());
    if (!paragraph.length) {
      paragraph.push(lines[index]);
      index += 1;
    }
    const element = document.createElement('p');
    appendInline(element, paragraph.join(' '));
    container.append(element);
  }
}
