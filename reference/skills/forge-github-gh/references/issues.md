# Issues

- **Read an issue**: `gh issue view <ref> --json number,title,body,state,labels,comments,url`, adding `-R <org>/<repo>` when `<ref>` is a bare number rather than a URL. Then `gh api --paginate repos/<owner>/<repo>/issues/<number>/timeline`, with `<owner>`, `<repo>`, and `<number>` from the issue's `url`. The timeline's `cross-referenced` and `connected` events name the issues and pull requests linked to it. Read each by its URL: an issue as above, a pull request as below.
- **Read a linked or found pull request**: `gh pr view <url> --json number,title,body,state,url`.
- **Search related issues**: `gh issue list -R <org>/<repo> --search '<terms>' --state all --limit 20 --json number,title,state,url`.
- **Search related pull requests**: `gh pr list -R <org>/<repo> --search '<terms>' --state all --limit 20 --json number,title,state,url`.

`<org>`/`<repo>` are `identity.org`/`identity.repo`. `<terms>` holds no `'`, so the single quotes around it hold.
