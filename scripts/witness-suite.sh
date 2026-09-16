#!/usr/bin/env bash
# Run every test the command coverage ledger's parity claims rest on.
#
# `tests/mcp_coverage.py` maps each registered command to the test that drives it over the real
# transports. `tests/test_mcp_coverage.py::resolves()` checks only that a function of that name
# exists in that file -- it does not import, collect or run it. So a witness that exists and
# CANNOT RUN resolves fine, and that is not hypothetical: five witness tests once spent a day
# dying inside their MCP client setup, before driving a single command, while the commands they
# witness sat in rows the ledger was happy with.
#
# A ledger whose witnesses are never executed is a claim, not evidence. This executes them.
# The node ids are derived from the ledger at the moment you ask, so the set cannot drift from
# what the ledger claims: a command that gains a witness tomorrow is covered the day it lands.
#
# Usage:  scripts/witness-suite.sh [checkout]
#   BOOKFLOW_PY=...              interpreter to run pytest with (default: python3)
#   BOOKFLOW_WITNESS_JOBS=N      how many at a time (default 4; each spawns an MCP child)
#   BOOKFLOW_WITNESS_OUT=DIR     where logs and basetemps go
#
# Exits non-zero if any witness did not pass, so it can gate a release.

set -uo pipefail
TREE="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
PY="${BOOKFLOW_PY:-python3}"
JOBS="${BOOKFLOW_WITNESS_JOBS:-4}"
OUT="${BOOKFLOW_WITNESS_OUT:-${TMPDIR:-/tmp}/bookflow-witnesses-$(date +%Y%m%d-%H%M%S)}"
cd "$TREE" || { echo "no such checkout: $TREE" >&2; exit 2; }
mkdir -p "$OUT"
export PYTHONPATH="$TREE/src:$TREE"

mapfile -t NODES < <("$PY" -c "
import tests.mcp_coverage as ledger
print('\n'.join(sorted({row['execution_witness'] for row in ledger.execution_map()
                        if row['execution_witness']})))
" 2>/dev/null)

if [ "${#NODES[@]}" -eq 0 ]; then
    echo "could not read the ledger -- is $TREE importable with this interpreter?" >&2
    exit 2
fi

echo "WITNESS SUITE — ${#NODES[@]} tests the coverage ledger cites, derived from it just now"
echo "  checkout $TREE   jobs $JOBS   logs $OUT"
echo

started=$(date +%s)
# One marker file per witness that did not pass, written from pytest's own exit status.
# Counting failures by grepping logs afterwards misses the case that matters most: a run
# killed or crashed before pytest printed any summary leaves a log matching no failure
# pattern, and reads as success. A gate that cannot see a dead run is not a gate.
printf '%s\n' "${NODES[@]}" | xargs -P "$JOBS" -I{} bash -c '
  node="{}"; slug=$(echo "$node" | tr "/:" "__"); log='"$OUT"'/"$slug".log
  '"$PY"' -m pytest "$node" -q --timeout=900 --basetemp='"$OUT"'/"$slug" >"$log" 2>&1
  status=$?
  line=$(grep -oE "[0-9]+ (passed|failed|error)[^ ]*" "$log" | tail -1)
  if [ $status -eq 0 ]; then printf "  PASS  %-92s %s\n" "$node" "$line"
  else
    echo "$node" >'"$OUT"'/"$slug".notpassed
    printf "  FAIL  %-92s %s\n" "$node" "${line:-exit $status, no pytest summary -- the run died}"
  fi
'
elapsed=$(( $(date +%s) - started ))

shopt -s nullglob
markers=("$OUT"/*.notpassed)
fails=${#markers[@]}
echo
printf 'ran %d witnesses in %dm%02ds — %d did not pass\n' "${#NODES[@]}" $((elapsed/60)) $((elapsed%60)) "$fails"
if [ "$fails" -gt 0 ]; then
    echo; echo "did not pass:"
    for marker in "${markers[@]}"; do
        printf '  %s\n     %s\n' "$(cat "$marker")" "${marker%.notpassed}.log"
    done
fi
[ "$fails" -eq 0 ]
