#!/usr/bin/env bash
# Freeze one Apple Mail account directory to an archive, then prove the copy is
# byte-identical to the source.
#
#   ./snapshot_account.sh <AccountUUID> <ARCHIVE_DIR> [--no-checksum]
#
# Do this before any export. It is ~1 minute for 15 GB on an internal SSD and it
# converts every later mistake from "lost mail" into "delete the copy and redo".
#
# Why ditto and not rsync: /usr/bin/rsync on current macOS is openrsync, whose
# -a expands to only -Dgloprt. It silently drops every extended attribute and
# exits 0 while doing it, and it also loses APFS compression. ditto preserves
# xattrs, ACLs, resource forks and compression. Verified on real Mail data.
# Do not "improve" this by switching to rsync.
#
# Read-only on the source. Never writes inside ~/Library/Mail.

set -uo pipefail

UUID="${1:-}"
ARCHIVE="${2:-}"
CHECKSUM=1
[ "${3:-}" = "--no-checksum" ] && CHECKSUM=0

if [ -z "$UUID" ] || [ -z "$ARCHIVE" ]; then
  echo "usage: $0 <AccountUUID> <ARCHIVE_DIR> [--no-checksum]" >&2
  exit 64
fi

SRC=""
for V in "$HOME/Library/Mail"/V*; do
  [ -d "$V/$UUID" ] && SRC="$V/$UUID"
done
if [ -z "$SRC" ]; then
  echo "account $UUID not found under $HOME/Library/Mail/V*" >&2
  echo "run ./discover_accounts.py to list valid UUIDs" >&2
  exit 66
fi

DST="$ARCHIVE/01-raw-snapshot/$UUID"
VERIFY="$ARCHIVE/04-verification"
mkdir -p "$(dirname "$DST")" "$VERIFY"
LOG="$ARCHIVE/00-snapshot.log"

echo "source : $SRC"
echo "dest   : $DST"

if [ -e "$DST" ]; then
  echo "refusing to overwrite an existing snapshot at $DST" >&2
  echo "move or delete it first, so an earlier good copy is never clobbered" >&2
  exit 73
fi

SRC_FREE=$(df -h "$HOME" | tail -1 | awk '{print $4}')
echo "free space before: $SRC_FREE"
echo "=== snapshot started $(date) ===" > "$LOG"
echo "source: $SRC" >> "$LOG"

# --rsrc --extattr keep resource forks and xattrs; Mail files carry
# com.apple.quarantine and occasionally FinderInfo.
/usr/bin/ditto --rsrc --extattr "$SRC" "$DST" >> "$LOG" 2>&1
RC=$?
echo "ditto exit=$RC finished $(date)" >> "$LOG"
if [ "$RC" -ne 0 ]; then
  echo "ditto FAILED (exit $RC); see $LOG" >&2
  exit "$RC"
fi

# NUL-delimited counting: some attachment filenames contain literal newlines,
# which makes any line-based count wrong.
count_files() { find "$1" -type f -print0 | tr -dc '\0' | wc -c | tr -d ' '; }
count_emlx()  { find "$1" -name '*.emlx' -print0 | tr -dc '\0' | wc -c | tr -d ' '; }

S_FILES=$(count_files "$SRC");  D_FILES=$(count_files "$DST")
S_EMLX=$(count_emlx "$SRC");    D_EMLX=$(count_emlx "$DST")

printf '\n%-22s %12s %12s\n' "" "source" "snapshot"
printf '%-22s %12s %12s\n' "files"    "$S_FILES" "$D_FILES"
printf '%-22s %12s %12s\n' "messages" "$S_EMLX"  "$D_EMLX"
printf '%-22s %12s %12s\n' "size" "$(du -sh "$SRC" | cut -f1)" "$(du -sh "$DST" | cut -f1)"

FAIL=0
[ "$S_FILES" != "$D_FILES" ] && { echo "FILE COUNT MISMATCH" >&2; FAIL=1; }
[ "$S_EMLX"  != "$D_EMLX"  ] && { echo "MESSAGE COUNT MISMATCH" >&2; FAIL=1; }

if [ "$CHECKSUM" -eq 1 ]; then
  echo
  echo "hashing both trees (this is the real proof; counts alone are not)..."
  ( cd "$SRC" && find . -type f -print0 | sort -z \
      | xargs -0 shasum -a 256 ) > "$VERIFY/checksums-source.txt" 2>/dev/null
  ( cd "$DST" && find . -type f -print0 | sort -z \
      | xargs -0 shasum -a 256 ) > "$VERIFY/checksums-snapshot.txt" 2>/dev/null
  if diff -q "$VERIFY/checksums-source.txt" "$VERIFY/checksums-snapshot.txt" \
       >/dev/null; then
    echo "RESULT: BYTE-IDENTICAL across $(wc -l < "$VERIFY/checksums-source.txt" \
      | tr -d ' ') hashed files"
  else
    echo "RESULT: CHECKSUM DIFFERENCES" >&2
    diff "$VERIFY/checksums-source.txt" "$VERIFY/checksums-snapshot.txt" | head -20
    FAIL=1
  fi
  # The hashed-line count can sit a few below the file count: filenames
  # containing newlines span multiple lines in shasum output. Not an error.
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "snapshot verified. export from:"
  echo "  $DST"
  exit 0
fi
echo "snapshot verification FAILED - do not proceed to export" >&2
exit 1
