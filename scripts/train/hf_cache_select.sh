# hf_cache_select.sh — put the HuggingFace cache on a disk that has room, and
# refuse to start when none does. Sourceable; sets and exports HF_HOME.
#
# R-F4350 (C-295). R-F4347 fixed this in ONE script. NINETEEN carried the same
# line, which is how a fix becomes a guard scoped to one file that silently
# certifies the rest. (My own first count was seven, because I read a truncated
# grep — the number to trust is the test below this file, not a headcount.)
#
# THE ORIGINAL DEFECT (R-F4347 / C-291), preserved here because this is now the
# only place it is explained. Every one of those scripts pinned the cache to
# `/workspace/.cache/huggingface` under a comment calling it "container disk".
#
# It is not the container disk. Measured on the pod of record, 2026-08-26,
# mid-failure:
#
#     /dev/md0   20G   20G  1.5M  100%  /workspace     <- a 20G VOLUME
#     overlay   122G   16M  122G    1%  /              <- the container disk
#
# A 7B base is ~15G, so the download filled the volume and died with
# "OSError: No space left on device (os error 28)" after pulling gigabytes of a
# paid GPU-hour. The comment is what let it survive review: it asserted the
# wrong disk was the right one, so a reader checking the line agreed with it.
# And because it used `export` rather than a `:-` default, it overwrote any
# inherited value — so the cache could not be redirected from outside either,
# on a pod, mid-incident.
#
# TWO PROPERTIES ARE LOAD-BEARING:
#   * An inherited HF_HOME WINS. A caller that has already chosen a disk knows
#     something this file does not.
#   * It FAILS CLOSED. Refusing in one second with the number beats dying at
#     ENOSPC ten minutes in — and silently picking a too-small disk is the
#     failure this exists to prevent, so "could not decide" must stop the run.
#
# Override HF_CACHE_CANDIDATES to change the search set; HF_MIN_FREE_MB to
# change the requirement (default 18000 — ~15G for a 7B in bf16 plus unpack).

hf_free_mb(){ df -Pm "$1" 2>/dev/null | awk 'NR==2{print $4+0}'; }

hf_cache_select(){
  HF_CACHE_CANDIDATES="${HF_CACHE_CANDIDATES:-/workspace/.cache/huggingface /root/.cache/huggingface}"
  HF_MIN_FREE_MB="${HF_MIN_FREE_MB:-18000}"

  if [ -z "${HF_HOME:-}" ]; then
    _best=""; _best_free=0
    for _cand in $HF_CACHE_CANDIDATES; do
      mkdir -p "$_cand" 2>/dev/null || continue
      _f=$(hf_free_mb "$_cand"); [ -z "$_f" ] && _f=0
      if [ "$_f" -gt "$_best_free" ]; then _best="$_cand"; _best_free="$_f"; fi
    done
    export HF_HOME="${_best:-/workspace/.cache/huggingface}"
    echo "[hf-cache] HF_HOME=$HF_HOME (${_best_free} MB free)"
  else
    mkdir -p "$HF_HOME" 2>/dev/null || true
    echo "[hf-cache] HF_HOME=$HF_HOME (inherited)"
  fi

  _free=$(hf_free_mb "$HF_HOME"); [ -z "$_free" ] && _free=0
  if [ "$_free" -lt "$HF_MIN_FREE_MB" ]; then
    echo "[FATAL] HF_HOME=$HF_HOME has ${_free} MB free, need ${HF_MIN_FREE_MB} MB." >&2
    echo "        A 7B base is ~15G. Free space, or set HF_HOME to a bigger disk." >&2
    df -Pm / /workspace 2>/dev/null >&2
    return 1
  fi
  return 0
}
