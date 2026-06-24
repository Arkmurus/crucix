#!/bin/sh
for d in $(ls -t /data/coder_workspace/ | head -10); do
  count=$(ls /data/coder_workspace/$d/ 2>/dev/null | wc -l)
  echo "$d: $count files"
done
