export function gameListTitlePending(entry:{status?:string|null;title?:string|null}) {
  return entry.status==='planning'&&(!entry.title?.trim()||/^Steam \d+$/i.test(entry.title.trim()));
}
