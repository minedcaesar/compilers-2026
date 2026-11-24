#!/usr/bin/env bash
# Compiles and runs every A1 example (unoptimised and with --opt), checking the EXPECT header.
set -u
cd "$(dirname "$0")/.."
cargo build -q -p rwetac -p runtime 2>/dev/null || { echo "build failed"; exit 1; }
fail=0
for f in a1/examples/*.eta; do
  want=$(grep -o 'EXPECT: -\?[0-9]*' "$f" | cut -d' ' -f2)
  for opt in "" "--opt"; do
    RUST_LOG=error ./target/debug/rwetac $opt "$f" >/dev/null || { echo "FAIL $f $opt (compile)"; fail=1; continue; }
    got=$(./target/debug/runtime "${f%.eta}.wat" | sed -n 's/Result : \[I64(\(-\?[0-9]*\))\]/\1/p')
    if [ "$got" = "$want" ]; then echo "ok   $(basename "$f") ${opt:-(no opt)} = $got"
    else echo "FAIL $(basename "$f") ${opt:-(no opt)}: got $got, want $want"; fail=1; fi
  done
  rm -f "${f%.eta}.wat"
done
exit $fail
