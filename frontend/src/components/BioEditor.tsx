import {
  BlockTypeSelect,
  BoldItalicUnderlineToggles,
  Button,
  CodeToggle,
  codeBlockPlugin,
  CreateLink,
  DiffSourceToggleWrapper,
  GenericDirectiveEditor,
  InsertCodeBlock,
  InsertImage,
  ListsToggle,
  MDXEditor,
  NestedLexicalEditor,
  StrikeThroughSupSubToggles,
  diffSourcePlugin,
  directivesPlugin,
  headingsPlugin,
  imagePlugin,
  linkDialogPlugin,
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

function InsertEmbedButton({
  kind,
  symbol,
  editorRef,
  onMarkdownChange,
}: {
  kind: 'youtube' | 'video';
  symbol: ReactNode;
  editorRef: RefObject<MDXEditorMethods | null>;
  onMarkdownChange: (markdown: string) => void;
}) {
  const publishMarkdown = usePublisher(markdown$);
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const label = kind === 'youtube' ? 'Insert YouTube video' : 'Insert media/video';
  const titleId = `bio-${kind}-dialog-title`;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      inputRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
      buttonRef.current?.focus();
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
    const currentMarkdown = editorRef.current?.getMarkdown() ?? '';
    const separator = currentMarkdown
      ? (currentMarkdown.endsWith('\n\n') ? '' : '\n\n')
      : '';
    const updatedMarkdown = `${currentMarkdown}${separator}:${kind}[${value.replace(/\]/g, '%5D')}]\n`;
    editorRef.current?.setMarkdown(updatedMarkdown);
    publishMarkdown(updatedMarkdown);
    onMarkdownChange(updatedMarkdown);
    setUrl('');
    closeDialog();
  }

  return (
    <>
      <Button ref={buttonRef} type="button" title={label} aria-label={label} onClick={() => setOpen(true)}>
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
          <label htmlFor={`bio-${kind}-url`}>{kind === 'youtube' ? 'YouTube URL' : 'Video or embed URL'}</label>
          <input
            ref={inputRef}
            id={`bio-${kind}-url`}
            type="url"
            required
            value={url}
            placeholder={kind === 'youtube' ? 'https://www.youtube.com/watch?v=…' : 'https://example.com/video.mp4'}
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

function CrossedEyeIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A10.8 10.8 0 0 1 12 5c5 0 8.8 4.1 10 7-.4 1-1.3 2.3-2.5 3.5M6.2 6.2C3.8 7.7 2.5 10 2 12c1.2 2.9 5 7 10 7 1.3 0 2.4-.3 3.4-.8" /></svg>;
}

function CameraIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Z" /><circle cx="12" cy="13" r="3.5" /></svg>;
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
    <DiffSourceToggleWrapper options={['rich-text', 'source']} SourceToolbar={<span className="bio-source-label">Raw Markdown</span>}>
      <BlockTypeSelect />
      <span className="bio-toolbar-divider" aria-hidden="true" />
      <BoldItalicUnderlineToggles />
      <StrikeThroughSupSubToggles options={['Strikethrough']} />
      <InsertSyntaxButton title="Spoiler" symbol={<CrossedEyeIcon />} syntax=":spoiler[spoiler text]" />
      <span className="bio-toolbar-divider" aria-hidden="true" />
      <CreateLink />
      <InsertImage />
      <InsertEmbedButton kind="youtube" symbol="▶" editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
      <InsertEmbedButton kind="video" symbol={<CameraIcon />} editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
      <span className="bio-toolbar-divider" aria-hidden="true" />
      <ListsToggle options={['number', 'bullet']} />
      <CenterAlignButton editorRef={editorRef} onMarkdownChange={onMarkdownChange} />
      <InsertSyntaxButton title="Block quote" symbol="❞" syntax={'\n\n> quote\n\n'} />
      <CodeToggle />
      <InsertCodeBlock />
    </DiffSourceToggleWrapper>
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
    linkDialogPlugin(),
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
        <span className="bio-mode-hint">Rich text editor · Raw Markdown source</span>
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
