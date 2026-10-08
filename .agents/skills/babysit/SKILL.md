---
name: babysit
description: Watch a pull request or review cycle until it is ready to merge. Use when asked to babysit, monitor, or keep checking PR comments, automated reviews, and CI until all actionable issues are resolved.
---

# Babysit PR

Stay with the PR until it is actually clean. Do not stop after one check pass if comments or review threads are still unresolved.

## Workflow

1. Identify the PR number, branch, and base branch.
2. Confirm the PR is not draft and inspect mergeability, checks, review decision, comments, review threads, and any expected automated reviewers. Record the current head SHA. Apply the Codex review-request rule below before waiting for reviews.
3. Watch pending checks and expected automated reviews until they finish for the current head. Poll at a practical interval, usually 30-60 seconds unless the user asks for a different cadence.
4. Read new comments and unresolved review threads. Treat bot summaries as useful, but verify actionable findings against the code.
5. Fix real issues in focused commits, run relevant tests/builds, push, and return to step 2.
6. Resolve stale review threads only after verifying the code or generated artifact now addresses the comment.
7. Declare the PR clean only when checks are passing or intentionally skipped, review decision is acceptable, expected automated reviews have completed for the current head (or were explicitly waived by the user), no actionable comments remain, and no unresolved review threads remain.

## Automated Review Completion

- Request a Codex review by posting `@codex review` on the PR unless the user explicitly instructs otherwise or Codex is already reviewing the current head. Invoking this skill authorizes this review-request comment; no additional confirmation is needed.
- Before posting, inspect existing review requests and their reactions. A Codex reviewer-bot eyes reaction (👀) on the PR body or a review-request comment indicates an agent is already reviewing; wait for that review instead of posting a duplicate request. Verify that the active review covers the current head using the head-association rules below. A human eyes reaction or a retained reaction from an older, completed review does not satisfy this exception.
- After a push, apply the same rule to the new head: request review via `@codex review` unless the user has waived it or Codex is already reviewing that head. If your request has not yet been acknowledged, wait within the review waiting window below instead of repeatedly posting requests.
- Identify automated reviewers configured for this PR or requested in its comments. A reviewer may report through reactions or comments without creating a required status check or a formal approval.
- Inspect reactions on the PR body and review-request comments through the GitHub API, including the reacting account. The coarse status and review-thread queries below do not include reactions.
- For Codex connector reviews, a reviewer-bot eyes reaction (👀) signals that review is underway; a reviewer-bot thumbs-up (👍) signals a clean result; findings arrive as comments or review threads. Use the actual reviewer's convention for other bots. Human reactions do not establish automated review completion.
- Treat eyes with no subsequent completion evidence as pending. A newer completed review can supersede a retained eyes reaction; do not wait forever merely because the old reaction remains.
- Tie completion to the current head SHA using review commit IDs, check runs, or a review request associated with that head. An old thumbs-up or an old clean summary is not approval of a newer push. PR-wide reactions without a reliable connection to the latest head are insufficient; timestamps alone do not prove which changes were reviewed.
- After every push, recheck whether the reviewer is scheduled or requested to review the new head. Process its new findings and repeat the cycle after fixes. A findings comment is actionable output, not a clean result; verify that the review has finished as well as that its findings are addressed.
- If an expected review fails, stalls, or has no verifiable completion evidence, report it as pending or unverified, not clean. Allow a reasonable waiting window (default: about 10 minutes without meaningful progress, unless the user specifies otherwise), then report the blocker and next step rather than polling indefinitely. The rule above authorizes the initial Codex request for each head; ask the user before retrying a failed or stalled request or triggering other reviewers unless already authorized.

## GitHub CLI Checks

Use `gh pr view` for the coarse status:

```bash
gh pr view <number> --json \
  number,state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,statusCheckRollup,url
```

Resolve the repository owner/name before using GraphQL:

```bash
repo_json=$(gh repo view --json owner,name)
owner=$(jq -r '.owner.login // .owner.name' <<<"$repo_json")
repo=$(jq -r '.name' <<<"$repo_json")
```

Use GraphQL for unresolved review threads. Include `pageInfo`; omit `cursor` on the first page, then pass the previous `endCursor` with `-f cursor="$cursor"` while `hasNextPage` is `true`.

```bash
gh api graphql \
  -f query='query($owner:String!,$repo:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){pageInfo{hasNextPage endCursor}nodes{id,isResolved,isOutdated,path,line,comments(last:1){nodes{author{login},body,createdAt,url}}}}}}}' \
  -f owner="$owner" -f repo="$repo" -F number=<number>
```

Use this loop when a PR may have many review threads:

```bash
thread_query='query($owner:String!,$repo:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){pageInfo{hasNextPage endCursor}nodes{id,isResolved,isOutdated,path,line,comments(last:1){nodes{author{login},body,createdAt,url}}}}}}}'
cursor_args=()

while :; do
  page=$(gh api graphql -f query="$thread_query" -f owner="$owner" -f repo="$repo" -F number=<number> "${cursor_args[@]}")
  printf '%s\n' "$page" | jq -r '.data.repository.pullRequest.reviewThreads.nodes[]
    | select(.isResolved==false)
    | [.id,.path,(.line//""),(.isOutdated|tostring),(.comments.nodes[-1].author.login//""),(.comments.nodes[-1].body|gsub("\n";" ")|.[0:240])]
    | @tsv'

  jq -e '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' >/dev/null <<<"$page" || break
  cursor=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$page")
  cursor_args=(-f cursor="$cursor")
done
```

Filter unresolved threads with `jq`:

```bash
jq -r '.data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved==false)
  | [.id,.path,(.line//""),(.isOutdated|tostring),(.comments.nodes[-1].author.login//""),(.comments.nodes[-1].body|gsub("\n";" ")|.[0:240])]
  | @tsv'
```

Resolve a stale thread only when the fix is verified:

```bash
gh api graphql \
  -f query='mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId}){thread{id,isResolved}}}' \
  -f threadId=<thread-id>
```

## Operating Rules

- Keep the watcher running while long checks are pending.
- If a generated file is part of the distribution, verify the source and generated artifact agree before resolving comments.
- If a bot reports an issue against stale code, confirm whether the thread is outdated or addressed in the latest head.
- Before final reporting, do one fresh sweep of PR status, unresolved threads, recent comments, automated-review completion for the latest head, and local `git status`.
- Report concrete evidence: latest commit SHA, check names and results, automated reviewer identity and completion evidence for that SHA, unresolved thread count, tests run, and any dirty local files left untouched.
