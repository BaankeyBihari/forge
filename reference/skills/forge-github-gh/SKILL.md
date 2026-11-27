---
name: forge-github-gh
description: The gh commands Forge roles run against GitHub issues, pull requests, and workflow runs, and the rules every gh call shares. Preloaded by roles that use GitHub.
---
<!-- @spec GH-LAYOUT-001, GH-LAYOUT-002 -->

# Forge GitHub `gh` commands

This skill says how to carry out a GitHub operation: the command, its fields, and what the output means. Your role skill says what to do with the result.

Before running any command of a reference file, read that file from the `references/` directory beside this `SKILL.md`. Read only the files your mode uses.

| Reference file | Used by | Modes |
|---|---|---|
| `references/issues.md` | `forge-scout` | `PRE_PLAN`, `TRIAGE`, `IDEA` |
| `references/pulls.md` | `forge-scout` | `PR_FETCH` |
| `references/ci.md` | `forge-scout` | `CI_WATCH`, `CI_RERUN` |

## Rules for every `gh` call

- **Where to run.** In `identity.worktree_path`.
- **Which repository.** A command about an issue or pull request known by its URL takes the repository from that URL: pass the URL, or, in a `gh api` path, the owner, repository, and number in it. Every other command acting on a repository passes `-R <org>/<repo>`, with `<org>`/`<repo>` from `identity.org`/`identity.repo`: a search, a bare issue or pull request number, and every workflow-run command. Put `-R` after the command's subcommand words (`gh run rerun <id> -R <org>/<repo>`), never between `gh` and them. `gh api user` names no repository.
- **Output.** Ask for JSON with named fields (`--json <fields>`) wherever the command offers it, never `gh`'s human-readable tables.
- **Failure.** When a `gh` command exits non-zero, end the operation and return `ERROR` with `gh`'s message. When `gh` says it is not authenticated, say in the `ERROR` that the user must run `gh auth login`. Never run `gh auth` yourself.
- **Reads stay reads.** For a read, pass `gh api` no method other than `GET` (`-X`) and none of `-f`, `-F`, `--raw-field`, `--field`, `--input` (they switch it to `POST`). Never call `gh api graphql`.
- **Text goes in files.** Text a command sends (a body, a comment, a review) comes from a file in the session scratchpad (`--body-file`, `--input`), never inline.
