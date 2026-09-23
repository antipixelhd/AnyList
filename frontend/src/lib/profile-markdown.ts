import MarkdownIt from 'markdown-it';
import type StateBlock from 'markdown-it/lib/rules_block/state_block.mjs';
import type StateInline from 'markdown-it/lib/rules_inline/state_inline.mjs';
import type Token from 'markdown-it/lib/token.mjs';

type EmbedKind = 'youtube' | 'video';

const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com']);
const VIDEO_EXTENSIONS = new Set(['.mp4', '.webm', '.ogv', '.ogg']);

function parseHttpsUrl(value: string): URL | null {
  try {
    const url = new URL(value.trim());
    return url.protocol === 'https:' && !url.username && !url.password ? url : null;
  } catch {
    return null;
  }
}

function youtubeVideoId(value: string): string | null {
  const url = parseHttpsUrl(value);
  if (!url || !YOUTUBE_HOSTS.has(url.hostname.toLowerCase())) return null;

  let id = '';
  if (url.hostname.toLowerCase() === 'youtu.be') id = url.pathname.split('/').filter(Boolean)[0] ?? '';
  else if (/^\/(?:embed|shorts|live)\//.test(url.pathname)) id = url.pathname.split('/')[2] ?? '';
  else id = url.searchParams.get('v') ?? '';

  return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null;
}

function safeVideoSource(value: string): string | null {
  const url = parseHttpsUrl(value);
  if (!url) return null;

  const path = url.pathname.toLowerCase();
  if ([...VIDEO_EXTENSIONS].some((extension) => path.endsWith(extension))) return url.href;

  if (url.hostname.toLowerCase() === 'vimeo.com' || url.hostname.toLowerCase() === 'www.vimeo.com') {
    const match = url.pathname.match(/^\/(\d+)\/?$/);
    if (match) return `https://player.vimeo.com/video/${match[1]}`;
  }

  return null;
}

function addInlineChildren(md: MarkdownIt, token: Token, source: string, env: unknown): void {
  token.children = [];
  md.inline.parse(source, md, env, token.children);
}

function parseProfileInline(state: StateInline, silent: boolean): boolean {
  const remaining = state.src.slice(state.pos, state.posMax);
  const tag = remaining.match(/^<u>([\s\S]*?)<\/u>/i);
  if (tag) {
    if (silent) return true;
    const token = state.push('profile_inline', 'u', 0);
    token.meta = { kind: 'underline' };
    addInlineChildren(state.md, token, tag[1], state.env);
    state.pos += tag[0].length;
    return true;
  }

  const directive = remaining.match(/^:(spoiler|youtube|video)\[((?:[^\[\]\\]|\\.|\[[^\]]*\])*)\]/i);
  if (!directive) return false;
  if (silent) return true;

  const kind = directive[1].toLowerCase() as EmbedKind | 'spoiler';
  const content = directive[2].replace(/\\([\[\]\\])/g, '$1').trim();
  const token = state.push('profile_inline', 'span', 0);
  token.meta = { kind, source: content };
  if (kind === 'spoiler') addInlineChildren(state.md, token, content, state.env);
  state.pos += directive[0].length;
  return true;
}

function parseProfileAlignment(state: StateBlock, startLine: number, endLine: number, silent: boolean): boolean {
  const opening = state.getLines(startLine, startLine + 1, state.blkIndent, false).trim().match(/^:::(center|left|right)$/i);
  if (!opening) return false;

  const alignment = opening[1].toLowerCase();
  let closeLine = startLine + 1;
  while (closeLine < endLine && state.getLines(closeLine, closeLine + 1, state.blkIndent, false).trim() !== ':::') closeLine += 1;
  if (closeLine >= endLine) return false;
  if (silent) return true;

  const token = state.push('profile_alignment', 'div', 0);
  token.block = true;
  token.meta = { alignment };
  const content = state.getLines(startLine + 1, closeLine, state.blkIndent, true);
  token.children = state.md.parse(content, state.env);
  state.line = closeLine + 1;
  return true;
}

const parser = new MarkdownIt({ html: false, linkify: false });
parser.inline.ruler.before('text', 'profile_inline', parseProfileInline);
parser.block.ruler.before('paragraph', 'profile_alignment', parseProfileAlignment, { alt: ['paragraph', 'reference'] });

parser.renderer.rules.profile_inline = (tokens, index, options, env, renderer) => {
  const token = tokens[index];
  const meta = token.meta as { kind: string; source?: string };
  if (meta.kind === 'underline') return `<u>${renderer.renderInline(token.children ?? [], options, env)}</u>`;
  if (meta.kind === 'spoiler') {
    return `<label class="profile-bio-spoiler"><input type="checkbox" aria-label="Reveal spoiler"><span class="profile-bio-spoiler-label">Reveal spoiler</span><span class="profile-bio-spoiler-content">${renderer.renderInline(token.children ?? [], options, env)}</span></label>`;
  }
  if (meta.kind === 'youtube') {
    const id = youtubeVideoId(meta.source ?? '');
    if (id) return `<span class="profile-bio-embed profile-bio-embed-video"><iframe src="https://www.youtube-nocookie.com/embed/${id}" title="YouTube video" loading="lazy" referrerpolicy="strict-origin-when-cross-origin" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe></span>`;
    return `<span class="profile-bio-embed-invalid">${parser.utils.escapeHtml(meta.source ?? '')}</span>`;
  }
  if (meta.kind === 'video') {
    const source = safeVideoSource(meta.source ?? '');
    if (source?.startsWith('https://player.vimeo.com/video/')) {
      return `<span class="profile-bio-embed profile-bio-embed-video"><iframe src="${source}" title="Vimeo video" loading="lazy" referrerpolicy="strict-origin-when-cross-origin" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe></span>`;
    }
    if (source) return `<span class="profile-bio-embed profile-bio-embed-video"><video controls preload="metadata"><source src="${parser.utils.escapeHtml(source)}"></video></span>`;
    return `<span class="profile-bio-embed-invalid">Unsupported video URL</span>`;
  }
  return renderer.renderInline(token.children ?? [], options, env);
};

parser.renderer.rules.profile_alignment = (tokens, index, options, env, renderer) => {
  const token = tokens[index];
  const alignment = (token.meta as { alignment: string }).alignment;
  return `<div class="profile-bio-align profile-bio-align-${alignment}">${renderer.render(token.children ?? [], options, env)}</div>`;
};

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
