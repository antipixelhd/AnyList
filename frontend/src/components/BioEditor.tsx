import {
  BoldItalicUnderlineToggles,
  Button,
  CodeToggle,
  codeBlockPlugin,
  DiffSourceToggleWrapper,
  GenericDirectiveEditor,
  MDXEditor,
  NestedLexicalEditor,
  StrikeThroughSupSubToggles,
  diffSourcePlugin,
  directivesPlugin,
  headingsPlugin,
  imagePlugin,
  linkPlugin,
  listsPlugin,
  markdown$,
  markdownShortcutPlugin,
  quotePlugin,
  toolbarPlugin,
  insertMarkdown$,
  type DirectiveDescriptor,
  type DirectiveEditorProps,
  type MDXEditorMethods,
} from '@mdxeditor/editor';
import { usePublisher } from '@mdxeditor/gurx';
import '@mdxeditor/editor/style.css';
import '../styles/bio-editor.css';
import BioHeadingSelect from './BioHeadingSelect';
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode, type RefObject } from 'react';

type Props = { initialMarkdown: string; token: string };

function DirectiveEditor(props: DirectiveEditorProps) {
  return <GenericDirectiveEditor {...props} />;
}

function CenterDirectiveEditor() {
  return (
    <div className="bio-center-directive">
      <NestedLexicalEditor
        block
        contentEditableProps={{ className: 'bio-center-directive-content' }}
        getContent={(node: any) => node.children ?? []}
        getUpdatedMdastNode={(node: any, children: any[]) => ({ ...node, children })}
      />
    </div>
  );
}

const bioDirectives: DirectiveDescriptor[] = [
  ...['spoiler', 'youtube', 'video'].map((name) => ({
    name,
    type: 'textDirective' as const,
    attributes: [],
    hasChildren: true,
    testNode: (node: { name?: string }) => node.name === name,
    Editor: DirectiveEditor,
  })),
  {
    name: 'center',
    type: 'containerDirective',
    attributes: [],
    hasChildren: true,
    testNode: (node: { name?: string }) => node.name === 'center',
    Editor: CenterDirectiveEditor,
  },
  {
    name: 'left',
    type: 'containerDirective',
    attributes: [],
    hasChildren: true,
    testNode: (node: { name?: string }) => node.name === 'left',
    Editor: DirectiveEditor,
  },
  {
    name: 'right',
    type: 'containerDirective',
    attributes: [],
    hasChildren: true,
    testNode: (node: { name?: string }) => node.name === 'right',
    Editor: DirectiveEditor,
  },
];

function InsertSyntaxButton({
  title,
  symbol,
  syntax,
}: {
  title: string;
  symbol: ReactNode;
  syntax: string;
}) {
  const insertMarkdown = usePublisher(insertMarkdown$);
  return (
    <Button type="button" title={title} aria-label={title} onClick={() => insertMarkdown(syntax)}>
      <span className="bio-toolbar-glyph" aria-hidden="true">{symbol}</span>
    </Button>
  );
}

type UrlKind = 'link' | 'image' | 'youtube' | 'video';

function InsertUrlButton({
  kind,
  symbol,
  editorRef,
  onMarkdownChange,
}: {
  kind: UrlKind;
  symbol: ReactNode;
  editorRef: RefObject<MDXEditorMethods | null>;
  onMarkdownChange: (markdown: string) => void;
}) {
  const publishMarkdown = usePublisher(markdown$);
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const label = {
    link: 'Insert link', image: 'Insert image',
    youtube: 'Insert YouTube video', video: 'Insert media/video',
  }[kind];
  const titleId = `bio-${kind}-dialog-title`;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      inputRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
      editorRef.current?.focus(undefined, { defaultSelection: 'rootEnd' });
    }
  }, [open]);

  function closeDialog() {
    setOpen(false);
    setError('');
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = url.trim();
    if (!value) {
      setError('Enter a URL to continue.');
      inputRef.current?.focus();
      return;
    }
    let normalizedUrl: string;
    try {
      const parsed = new URL(value);
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Invalid protocol');
      if (kind !== 'link' && parsed.protocol !== 'https:') throw new Error('HTTPS required');
      normalizedUrl = parsed.href.replace(/\(/g, '%28').replace(/\)/g, '%29');
    } catch {
      setError(kind === 'link' ? 'Enter a valid web URL.' : 'Enter a valid HTTPS URL.');
      inputRef.current?.focus();
      return;
    }
    const escapedDescription = description.trim().replace(/\\/g, '\\\\').replace(/\[/g, '\\[').replace(/\]/g, '\\]');
    const syntax = kind === 'link' ? `[${escapedDescription || normalizedUrl}](${normalizedUrl})`
      : kind === 'image' ? `![${escapedDescription}](${normalizedUrl})`
      : `:${kind}[${normalizedUrl.replace(/\]/g, '%5D')}]`;
    const currentMarkdown = editorRef.current?.getMarkdown() ?? '';
    const separator = currentMarkdown
      ? (currentMarkdown.endsWith('\n\n') ? '' : '\n\n')
      : '';
    const updatedMarkdown = `${currentMarkdown}${separator}${syntax}\n`;
    editorRef.current?.setMarkdown(updatedMarkdown);
    publishMarkdown(updatedMarkdown);
    onMarkdownChange(updatedMarkdown);
    setUrl('');
    setDescription('');
    closeDialog();
  }

  return (
    <>
      <Button type="button" title={label} aria-label={label} onClick={() => setOpen(true)}>
        <span className="bio-toolbar-glyph" aria-hidden="true">{symbol}</span>
      </Button>
      <dialog
        ref={dialogRef}
        className="bio-embed-dialog"
        aria-labelledby={titleId}
        onCancel={(event) => { event.preventDefault(); closeDialog(); }}
        onClick={(event) => { if (event.target === dialogRef.current) closeDialog(); }}
      >
        <form className="bio-embed-dialog-form" onSubmit={submit}>
          <div className="bio-embed-dialog-heading">
            <h3 id={titleId}>{label}</h3>
            <button type="button" className="bio-embed-dialog-close" aria-label="Close dialog" onClick={closeDialog}>×</button>
          </div>
          {(kind === 'link' || kind === 'image') && <>
            <label htmlFor={`bio-${kind}-description`}>{kind === 'link' ? 'Link text' : 'Image description'}</label>
            <input
              id={`bio-${kind}-description`}
              type="text"
              value={description}
              placeholder={kind === 'link' ? 'Text to display' : 'Describe the image'}
              onChange={(event) => setDescription(event.target.value)}
            />
          </>}
          <label htmlFor={`bio-${kind}-url`}>{kind === 'link' ? 'Link URL' : kind === 'image' ? 'Image URL' : kind === 'youtube' ? 'YouTube URL' : 'Video or embed URL'}</label>
          <input
            ref={inputRef}
            id={`bio-${kind}-url`}
            type="url"
            required
            value={url}
            placeholder={kind === 'youtube' ? 'https://www.youtube.com/watch?v=…' : kind === 'video' ? 'https://example.com/video.mp4' : 'https://example.com'}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? `bio-${kind}-error` : undefined}
            onChange={(event) => { setUrl(event.target.value); setError(''); }}
          />
          {error && <p className="bio-embed-dialog-error" id={`bio-${kind}-error`} role="alert">{error}</p>}
          <div className="bio-embed-dialog-actions">
            <button type="button" className="button-secondary" onClick={closeDialog}>Cancel</button>
            <button type="submit" className="button-primary">Insert</button>
          </div>
        </form>
      </dialog>
    </>
  );
}

function CameraIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Z" /><circle cx="12" cy="13" r="3.5" /></svg>;
}

function LinkIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13.5a5 5 0 0 0 7.1 0l2.1-2.1a5 5 0 0 0-7.1-7.1L10.5 6M14 10.5a5 5 0 0 0-7.1 0l-2.1 2.1a5 5 0 0 0 7.1 7.1l1.6-1.6" /></svg>;
}

function ImageIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8" cy="9" r="1.5" /><path d="m4 17 5-5 3 3 3-3 5 5" /></svg>;
}

function CenterAlignButton({ editorRef, onMarkdownChange }: {
  editorRef: RefObject<MDXEditorMethods | null>;
  onMarkdownChange: (markdown: string) => void;
}) {
  const publishMarkdown = usePublisher(markdown$);
  return (
    <Button
      type="button"
      title="Center align"
      aria-label="Center align"
      onClick={() => {
        const currentMarkdown = editorRef.current?.getMarkdown() ?? '';
        const separator = currentMarkdown ? (currentMarkdown.endsWith('\n') ? '\n' : '\n\n') : '';
        const updatedMarkdown = `${currentMarkdown}${separator}:::center\ncentered text\n:::\n`;
        editorRef.current?.setMarkdown(updatedMarkdown);
        publishMarkdown(updatedMarkdown);
        onMarkdownChange(updatedMarkdown);
        editorRef.current?.focus(undefined, { defaultSelection: 'rootEnd' });
      }}
    >
      <span className="bio-toolbar-glyph" aria-hidden="true">☰</span>
    </Button>
  );
}

function BioToolbar({ editorRef, onMarkdownChange }: {
  editorRef: RefObject<MDXEditorMethods | null>;
  onMarkdownChange: (markdown: string) => void;
}) {
  return (
    <div
      style={{ display: 'contents' }}
      onMouseDownCapture={(event) => {
        // Keep the editor's Lexical selection active while toolbar controls run.
        // Dialog inputs receive focus explicitly when their modal opens.
        if ((event.target as HTMLElement).closest('button')) {
          event.preventDefault();
          if (!document.activeElement?.closest('.bio-editor-content')) {
            editorRef.current?.focus(undefined, { defaultSelection: 'rootEnd' });
          }
        }
      }}
    >
      <DiffSourceToggleWrapper options={['rich-text', 'source']} SourceToolbar={<span className="bio-source-label">Raw Markdown</span>}>
        <BioHeadingSelect />
        <span className="bio-toolbar-divider" aria-hidden="true" />
        <BoldItalicUnderlineToggles />
        <StrikeThroughSupSubToggles options={['Strikethrough']} />
        <span className="bio-toolbar-divider" aria-hidden="true" />
        <InsertUrlButton kind="link" symbol={<LinkIcon />} editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
        <InsertUrlButton kind="image" symbol={<ImageIcon />} editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
        <InsertUrlButton kind="youtube" symbol="▶" editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
        <InsertUrlButton kind="video" symbol={<CameraIcon />} editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
        <span className="bio-toolbar-divider" aria-hidden="true" />
        <CenterAlignButton editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
        <InsertSyntaxButton title="Block quote" symbol="❞" syntax={'\n\n> quote\n\n'} />
        <CodeToggle />
      </DiffSourceToggleWrapper>
    </div>
  );
}

export default function BioEditor({ initialMarkdown, token }: Props) {
  const editorRef = useRef<MDXEditorMethods>(null);
  const [draft, setDraft] = useState(initialMarkdown);
  const [saved, setSaved] = useState(initialMarkdown);
  const [status, setStatus] = useState('');
  const [statusKind, setStatusKind] = useState<'saved' | 'saving' | 'error'>('saved');
  const [saving, setSaving] = useState(false);
  const tooLong = draft.length > 5000;

  const plugins = useMemo(() => [
    toolbarPlugin({ toolbarContents: () => <BioToolbar editorRef={editorRef} onMarkdownChange={setDraft} /> }),
    headingsPlugin(),
    listsPlugin(),
    quotePlugin(),
    linkPlugin(),
    imagePlugin(),
    codeBlockPlugin({ defaultCodeBlockLanguage: 'txt' }),
    directivesPlugin({ directiveDescriptors: bioDirectives }),
    diffSourcePlugin({ viewMode: 'rich-text' }),
    markdownShortcutPlugin(),
  ], []);

  useEffect(() => {
    const statusElement = document.querySelector<HTMLElement>('[data-save-state="bio"]');
    if (!statusElement) return;
    statusElement.textContent = status;
    statusElement.dataset.state = statusKind;
  }, [status, statusKind]);

  async function saveBio() {
    const markdown = editorRef.current?.getMarkdown() ?? draft;
    if (markdown.length > 5000) {
      setStatus('Bio must be 5,000 characters or fewer.');
      setStatusKind('error');
      return;
    }
    setSaving(true);
    setStatus('Saving…');
    setStatusKind('saving');
    try {
      const response = await fetch('/api/proxy/profile/me', {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ bio: markdown || null }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string | unknown[] };
        throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not save bio.');
      }
      setDraft(markdown);
      setSaved(markdown);
      setStatus('Saved');
      setStatusKind('saved');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not save bio.');
      setStatusKind('error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bio-editor-island" aria-labelledby="bio-label">
      <div className="bio-editor-meta">
        <span id="bio-count" aria-live="polite">{draft.length.toLocaleString()} / 5,000</span>
      </div>
      <MDXEditor
        ref={editorRef}
        markdown={initialMarkdown}
        onChange={(markdown) => {
          setDraft(markdown);
          if (markdown.length <= 5000 && statusKind === 'error') {
            setStatus('');
            setStatusKind('saved');
          }
        }}
        plugins={plugins}
        contentEditableClassName="bio-editor-content"
        placeholder="Write a short bio…"
        trim={false}
        translation={(key, defaultValue) => key === 'toolbar.source' ? 'Raw Markdown' : defaultValue}
        className="bio-mdx-editor"
      />
      <div className="bio-editor-actions">
        {tooLong && <span className="bio-editor-error" role="alert">Bio must be 5,000 characters or fewer.</span>}
        <button
          type="button"
          className="button-primary bio-save-button"
          disabled={saving || tooLong || draft === saved}
          onClick={saveBio}
        >
          {saving ? 'Saving…' : 'Save bio'}
        </button>
      </div>
    </div>
  );
}
