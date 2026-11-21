for s in m1 m1-extra local-20261120 syn640; do
  BR_DATA=/sets/$s python /work/ledger.py /sets/$s /work/out/$s.json 2>/work/out/$s.err
  BR_DATA=/sets/$s python /app/selector.py > /work/out/$s.sel 2>/dev/null
done
