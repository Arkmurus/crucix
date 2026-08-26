#!/usr/bin/env bash
# R-F4365 (C-311) — derive the R-tag the deploy banner reports.
#
# THE BANNER IS A CLAIM ABOUT THE BUILD, not a summary of commit messages.
# `/health/live` renders it as `build_rev`, and CLAUDE.md §11 makes that the
# anchor of deploy verification: "a deploy is NOT done until you have PROVEN it
# live ... CONFIRM the build_rev matches your commit".
#
# WHAT WAS WRONG. All four deploy workflows derived it from HEAD's subject alone:
#     R_TAG=$(git log -1 --pretty=%s | grep -oE 'R-F[0-9]+' | head -1)
#     R_TAG=${R_TAG:-no-r-tag}
# Observed live 2026-08-26: a build shipping R-F4362 AND R-F4364 rendered
# `no-r-tag - sha f70d073f`, because the commit at HEAD was a docs commit.
# "no-r-tag" reads as "this build ships nothing" on a build containing
# everything. `scripts/deploy.ps1` had already solved this three times over
# (R-F3357 / R-F3247 / R-F3371) and none of it reached the workflows — which §11
# now names the PRIMARY deploy path.
#
# WHY A BOUNDED WINDOW, NOT A SINCE-LAST-DEPLOY RANGE. deploy.ps1 ranges from the
# newest `deploy-*` tag, which works because IT creates those tags. The workflow
# path does not: measured here, the newest deploy tag is 257 commits and six days
# behind HEAD. Ranging from it would make every workflow deploy claim six days of
# R-numbers, and walking it costs ~500 git invocations. So this scans a bounded
# window of recent history and reports the R-numbers of the commits in it that
# actually ship something.
#
# THE SHA IS THE AUTHORITATIVE HALF. `build_rev` carries `<tag> - sha <commit>`,
# and the sha answers "is my commit live" exactly. The tag is the human-readable
# half, so the honest goal is "name what this build introduced" rather than a
# precise set the tagging gap makes uncomputable.
#
# Rules ported from deploy.ps1, each earned by a live misreport:
#   R-F3247  skip registry bookkeeping — `chore: reserve|mark|ship` touches the
#            ledger, not the image.
#   R-F3371  skip commits that ship NOTHING into the image (docs/, memory/,
#            *.md, the reservation ledgers), and take only the FIRST R-number in
#            a subject: subjects are "<type>: R-F#### - ...", so the first is what
#            the commit ships and any later one is prose citing earlier work.
#
# Prints the tag on stdout; `no-r-tag` only when the window genuinely holds no
# shipping commit — now a true statement rather than an artefact of reading one.
set -uo pipefail

WINDOW="${ARIA_R_TAG_WINDOW:-40}"     # commits to consider
MAX_TAGS="${ARIA_R_TAG_MAX:-4}"       # keep the banner readable

_ships_nothing='^(docs/|memory/|[^/]*[.]md$|data/[cr]_number_reservations[.]json$)'

# ONE git pass: subject + changed paths per commit, so this costs a single
# invocation instead of two per commit (the first draft spawned ~500 and timed
# out locally — a derivation that is too slow to run is not a derivation).
tags=$(git log -n "$WINDOW" --name-only --pretty=format:'@@%s' 2>/dev/null | awk -v pat="$_ships_nothing" '
  function flush_commit() {
    if (subj != "" && ships && rnum != "") print rnum
    subj = ""; ships = 0; rnum = ""
  }
  /^@@/ {
    flush_commit()
    subj = substr($0, 3)
    # R-F3247 — registry bookkeeping ships no code.
    if (subj ~ /^chore:[ ]*(reserve|mark|ship)/) { subj = ""; next }
    # R-F3371 — the FIRST R-number is the one this commit ships.
    if (match(subj, /R-F[0-9]+/)) rnum = substr(subj, RSTART, RLENGTH)
    next
  }
  NF == 0 { next }
  {
    # R-F3371 — any path outside the ships-nothing set means it reaches the image
    if ($0 !~ pat) ships = 1
  }
  END { flush_commit() }
' | awk '!seen[$0]++' | head -n "$MAX_TAGS" | tr '\n' '+' | sed 's/+$//')

printf '%s' "${tags:-no-r-tag}"
