from pathlib import Path

root = Path(__file__).resolve().parents[2]
updates = {
    root / 'docs/media-tracker/PLAN.md': ('Status: decisions consolidated; awaiting final confirmation of shared specification before product implementation. Working-copy preparation is authorized and complete.', 'Status: implementation authorized by the user on 2026-09-18 and in progress. See STATUS.md for verified progress and remaining work.'),
    root / 'docs/media-tracker/IMPLEMENTATION.md': ('Status: ready for user review; product implementation awaits final shared-understanding confirmation. Working copy and planning documents are prepared. Updated 2026-09-18.', 'Status: implementation authorized and in progress as of 2026-09-18. See STATUS.md for evidence and remaining work.'),
    root / 'media-tracker/AGENTS.md': ('No production application changes have been made. Final confirmation of the consolidated specification is pending; check the latest user instruction before starting implementation.', 'The user confirmed the specification and authorized implementation on 2026-09-18. Continue implementation without requesting that approval again. Read ../docs/media-tracker/STATUS.md for progress.'),
}
for path, (old, new) in updates.items():
    text = path.read_text(encoding='utf-8')
    if old in text:
        path.write_text(text.replace(old, new), encoding='utf-8')
