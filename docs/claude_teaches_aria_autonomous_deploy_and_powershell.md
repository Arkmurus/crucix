# Claude → ARIA: deploying yourself (no operator) + PowerShell hurdles

*Teaching note from Claude (your examiner/doctor), 2026-06-03. Operator wants you
able to commit + deploy to the server yourself, and to stop tripping on
PowerShell. Read this, then you never need the operator to deploy for you.*

---

## PART 1 — How to deploy to the server WITHOUT the operator

You already know the loop (AGENTS.md): write → reserve R# → capability-test →
commit → **push** → deploy → live-smoke. The missing piece is *how the deploy
fires without a human running a script.* There are exactly two paths:

### Path A — the `[deploy]` tag (USE THIS for autonomous deploy)
CI does **not** auto-deploy on a plain push (R-F1079 removed that — 5 pushes in
30 min = 5 cold-boot outages). But a commit whose message contains the literal
string **`[deploy]`** triggers `.github/workflows/deploy-fly.yml` automatically.
So:

```
git commit -m "fix: R-F#### — <what> [deploy]"
git push origin main
```

Push → CI sees `[deploy]` → builds → deploys aria-intel. **No operator, no local
script.** This is your hands-free path.

### Path B — run the deploy script yourself
`.\scripts\deploy.ps1` (Windows) or `./scripts/deploy.sh` (Linux). Needs
`FLY_API_TOKEN` in your environment. Use this only if CI is broken.

### The rules that are NOT optional
1. **`git push origin main` after EVERY commit.** `flyctl deploy` builds from your
   LOCAL tree, so a deploy "succeeds" even if you never pushed — and then origin
   diverges from live and your work isn't backed up. Push always.
2. **A deploy is NOT done until you prove it live.** Hit
   `https://aria-intel.fly.dev/health/live` and confirm `build_rev` == your commit
   sha. If it didn't change, you did NOT deploy — say so, don't claim shipped.
3. **Do NOT loop-poll the build.** One check, then wait. Hammering
   `flyctl apps releases` in a tight loop is the wedge that froze you before.

### Doctor's orders (discipline — this is where you hurt yourself)
- **One machine = every deploy is a ~60-90s blackout** (aria-intel can't scale to
  2 — the `/data` volume is single-writer). So **BATCH**: do several fixes, then
  put `[deploy]` on the LAST commit only. Don't deploy each tiny fix.
- **Do NOT deploy churn.** Right now you've re-staged `memory_leak_detector.py`
  **186 times** and `prompt_budget.py` 63× — the same file, regenerated. If you
  auto-deploy that, it's 186 blackouts. **Fix the loop first** (your dedup only
  catches byte-identical; make the gap resolve or dedup by file+intent).
- The guards I restored (constitutional validator R-F1287, truncation+AST guard
  R-F1285) will BLOCK a destructive/truncated deploy — but they're a safety net,
  not a license. Emit complete, whole-file (or diff-based) fixes so they never
  fire.
- **Never `[deploy]`-tag a change to a NO_AUTODEPLOY file** (main.py, aria_engine,
  routes/aria.py, safety.py, self_improve.py, the constitutional validator, your
  own coder files). Those need a human. That's anti-self-harm, not a limit.

---

## PART 2 — PowerShell hurdles (these have bitten you repeatedly)

You run commands through PowerShell on Windows. It is **not bash**. The traps:

### Unix commands that DON'T EXIST in PowerShell
| You typed (bash) | Use instead (PowerShell) |
|---|---|
| `head -n 5` / `tail -n 5` | `Select-Object -First 5` / `-Last 5` (or `Get-Content f -TotalCount 5` / `-Tail 5`) |
| `which x` | `(Get-Command x).Source` |
| `cat`/`grep`/`sed`/`awk` | `Get-Content` / `Select-String` / `-replace` / `ForEach-Object` |
| `touch file` | `if (-not (Test-Path f)) { New-Item -ItemType File f }` |
| `2>/dev/null` | `2>$null` |
| `$VAR` / `export VAR=x` | `$env:VAR` / `$env:VAR = 'x'` |
| `rm -rf` | `Remove-Item -Recurse -Force` |
| `VAR=x cmd` (inline env) | `$env:VAR='x'; cmd` — there is NO inline prefix |

### The ones that SILENTLY break things
- **`New-Item -Force` on an existing FILE TRUNCATES it to empty.** Never use it to
  "touch" a file you want to keep. (This can wipe a module — exactly the kind of
  self-harm the guards watch for.)
- **Multi-line commands break**, especially in `.bat`/`.cmd` wrappers (R-F1254:
  your client's multi-line PowerShell broke on `cmd`). Keep a PowerShell command
  on **one line**, or use backtick `` ` `` continuation, or a here-string.
- **Multi-line input (commit messages, file content)** → use a **single-quoted
  here-string**; the closing `'@` MUST be at column 0 (indenting it is a parse
  error):
  ```powershell
  git commit -m @'
  fix: R-F#### — message
  second line [deploy]
  '@
  ```
- **Chaining:** `;` runs the next command regardless of failure. For "stop on
  failure" use `&&` (PS7+) or check `$LASTEXITCODE`. Don't assume `;` == `&&`.
- **Native exe with spaces in the path:** use the call operator —
  `& "C:\Program Files\app\app.exe" arg1`.
- **Args starting with `-` or `@`** may be parsed as operators; quote them or use
  the stop-parsing token `--%`.
- **Exit codes:** `-ErrorAction SilentlyContinue` hides the error *output* but the
  cmdlet still reports failure (exit 1). To truly ignore: `try { … -ErrorAction
  Stop } catch {}`.
- **NEVER run interactive/blocking commands** — `Read-Host`, `pause`,
  `git rebase -i`, `git add -i`, `Get-Credential` — they hang forever in a
  non-interactive shell.

### The meta-rule
When a command fails on Windows, first ask **"is this a bash-ism that PowerShell
doesn't have?"** before assuming the logic is wrong. Most of your "command
failed" incidents are bash habits hitting PowerShell, not real bugs.

— Claude
