import MarkdownIt from 'markdown-it';
import type Token from 'markdown-it/lib/token.mjs';

const parser = new MarkdownIt({ html: false, linkify: false });

export function renderProfileBio(markdown: string): string {
  return parser.render(markdown);
}

export function plainBioExcerpt(markdown: string, maxLength = 140): string {
  const words: string[] = [];
  const visit = (tokens: Token[]) => {
    for (const token of tokens) {
      if (token.type === 'text' || token.type === 'code_inline' || token.type === 'code_block' || token.type === 'fence') words.push(token.content);
      else if (token.type === 'softbreak' || token.type === 'hardbreak') words.push(' ');
      if (token.children) visit(token.children);
    }
  };
  visit(parser.parse(markdown, {}));
  const text = words.join(' ').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1).trimEnd()}…` : text;
}
