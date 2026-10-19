export type LegalTextPart = {text: string; href?: string};

/** Support ordinary HTTPS links in private disclosure text without accepting HTML. */
export function legalTextParts(text: string): LegalTextPart[] {
  const parts: LegalTextPart[] = [];
  const links = /\[([^\]\r\n]+)\]\((https:\/\/[^()\s]+)\)/g;
  let position = 0;
  for (const match of text.matchAll(links)) {
    let url: URL;
    try { url = new URL(match[2]); } catch { continue; }
    if (url.username || url.password) continue;
    if (match.index > position) parts.push({text: text.slice(position, match.index)});
    parts.push({text: match[1], href: match[2]});
    position = match.index + match[0].length;
  }
  if (position < text.length) parts.push({text: text.slice(position)});
  return parts;
}
