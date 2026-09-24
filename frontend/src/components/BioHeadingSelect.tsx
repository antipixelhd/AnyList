import { $createHeadingNode, $createQuoteNode } from '@lexical/rich-text';
import { useCellValue, usePublisher } from '@mdxeditor/gurx';
import { $createParagraphNode } from 'lexical';
import { createPortal } from 'react-dom';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  activeEditor$,
  allowedHeadingLevels$,
  convertSelectionToNode$,
  currentBlockType$,
} from '@mdxeditor/editor';
import '../styles/bio-heading-select.css';

type BlockOption = { value: string; label: string };

const quoteOption: BlockOption = { value: 'quote', label: 'Quote' };

function getLabel(blockType: string): string {
  if (blockType === 'paragraph' || blockType === '') return 'Paragraph';
  if (blockType === 'quote') return 'Quote';
  const headingMatch = /^h([1-6])$/.exec(blockType);
  return headingMatch ? `Heading ${headingMatch[1]}` : 'Paragraph';
}

export default function BioHeadingSelect() {
  const activeEditor = useCellValue(activeEditor$);
  const currentBlockType = useCellValue(currentBlockType$);
  const allowedHeadingLevels = useCellValue(allowedHeadingLevels$);
  const convertSelectionToNode = usePublisher(convertSelectionToNode$);
  const [open, setOpen] = useState(false);
  const [popoverPosition, setPopoverPosition] = useState({ top: 0, left: 0 });
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const options: BlockOption[] = [
    { value: 'paragraph', label: 'Paragraph' },
    ...allowedHeadingLevels.map((level) => ({ value: `h${level}`, label: `Heading ${level}` })),
    quoteOption,
  ];

  const currentOptionIndex = Math.max(0, options.findIndex(({ value }) => value === currentBlockType));

  function updatePopoverPosition() {
    const bounds = triggerRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const estimatedHeight = Math.min(options.length * 36 + 8, window.innerHeight - 32);
    const belowTop = bounds.bottom + 5;
    const top = belowTop + estimatedHeight <= window.innerHeight - 8
      ? belowTop
      : Math.max(8, bounds.top - estimatedHeight - 5);
    setPopoverPosition({
      top,
      left: Math.max(8, Math.min(bounds.left, window.innerWidth - 176)),
    });
  }

  function openMenu() {
    updatePopoverPosition();
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    optionRefs.current[currentOptionIndex]?.focus();

    const handlePointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node
        && !rootRef.current?.contains(event.target)
        && !popoverRef.current?.contains(event.target)) {
        setOpen(false);
      }
    };
    const reposition = () => updatePopoverPosition();
    document.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
    };
  }, [open, currentOptionIndex]);

  function selectOption(option: BlockOption) {
    switch (option.value) {
      case 'paragraph':
        convertSelectionToNode(() => $createParagraphNode());
        break;
      case 'quote':
        convertSelectionToNode(() => $createQuoteNode());
        break;
      default:
        if (/^h[1-6]$/.test(option.value)) {
          convertSelectionToNode(() => $createHeadingNode(option.value as `h${1 | 2 | 3 | 4 | 5 | 6}`));
        }
    }
    setOpen(false);
    activeEditor?.focus();
  }

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
    }
  }

  function handleOptionKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      activeEditor?.focus();
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      const nextIndex = event.key === 'Home' ? 0
        : event.key === 'End' ? options.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      optionRefs.current[nextIndex]?.focus();
    }
  }

  return (
    <div className="bio-heading-select" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="bio-heading-select-trigger"
        aria-label="Block style"
        aria-haspopup="menu"
        aria-expanded={open}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => open ? setOpen(false) : openMenu()}
        onKeyDown={handleTriggerKeyDown}
      >
        <span>{getLabel(currentBlockType)}</span>
        <svg className="bio-heading-select-chevron" viewBox="0 0 16 16" aria-hidden="true">
          <path d="m4 6 4 4 4-4" />
        </svg>
      </button>
      {open && createPortal(
        <div
          ref={popoverRef}
          className="bio-heading-select-popover"
          role="menu"
          aria-label="Block style"
          style={{ top: popoverPosition.top, left: popoverPosition.left }}
        >
          {options.map((option, index) => (
            <button
              key={option.value}
              ref={(element) => { optionRefs.current[index] = element; }}
              type="button"
              role="menuitemradio"
              aria-checked={option.value === currentBlockType}
              className="bio-heading-select-option"
              onClick={() => selectOption(option)}
              onMouseDown={(event) => event.preventDefault()}
              onKeyDown={(event) => handleOptionKeyDown(event, index)}
            >
              <span>{option.label}</span>
              {option.value === currentBlockType && <span className="bio-heading-select-check" aria-hidden="true">✓</span>}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
