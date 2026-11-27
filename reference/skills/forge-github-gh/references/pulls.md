# Pull requests

- **Read a pull request**: `gh pr view <ref> --json number,title,body,author,baseRefName,headRefName,headRefOid,files,url`, adding `-R <org>/<repo>` when `<ref>` is a bare number rather than a URL.
- **Its diff**: `gh pr diff <ref>`, with `-R <org>/<repo>` when `<ref>` is a bare number.
- **Its review comments and reviews**: REST reads with `gh api --paginate --slurp` of `repos/<owner>/<repo>/pulls/<number>/comments` and `repos/<owner>/<repo>/pulls/<number>/reviews`, with `<owner>`, `<repo>`, and `<number>` from the pull request's `url`. Save each to a scratchpad file, then merge them into one JSON object `{"comments": [...], "reviews": [...]}`. `--slurp` wraps each page in a list, so flatten one level:

  ```bash
  python3 -c 'import json,sys; c,r=(sum(json.load(open(p)),[]) for p in sys.argv[1:3]); json.dump({"comments":c,"reviews":r},open(sys.argv[3],"w"),indent=1)' <comments.json> <reviews.json> <out.json>
  ```
- **The authenticated user**: `gh api user --jq .login`.

`<org>`/`<repo>` are `identity.org`/`identity.repo`.
