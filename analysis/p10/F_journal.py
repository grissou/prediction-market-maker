"""F_journal.py (explorer F, Package 10): per-hour write/defer/quote/fill counts from the bot journal.
Usage: python3 analysis/p10/F_journal.py [/home/claude/snap03]"""
import sys, gzip, re, collections, statistics as st
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
H = collections.defaultdict(lambda: collections.Counter())
defer, priced, resting, cyc = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
rx_def = re.compile(r"request budget: (\d+) of (\d+) order changes deferred")
rx_rt = re.compile(r"priced (\d+)/(\d+) \| resting (\d+) \| last cycle ([\d.]+) s")
rx_q = re.compile(r"fv [\d.]+.* \| bid (\S+) ask (\S+)")
for line in gzip.open(f"{D}/journal_2026-10-02_2037_to_now.log.gz", "rt", errors="replace"):
    h = line[:13]
    m = rx_def.search(line)
    if m:
        H[h]["defer_lines"] += 1; defer[h].append((int(m[1]), int(m[2]))); continue
    m = rx_rt.search(line)
    if m:
        priced[h].append(int(m[1])); resting[h].append(int(m[3])); cyc[h].append(float(m[4])); H[h]["rt"] += 1; continue
    if " FILL ex" in line: H[h]["fill"] += 1
    elif "TAKE " in line: H[h]["take"] += 1
    elif "Insufficient avail" in line: H[h]["rej_funds"] += 1
    elif "order rejected" in line: H[h]["rej_other"] += 1
    elif "BURST MODE" in line: H[h]["burst"] += 1
    elif "one cancel-all instead" in line: H[h]["cancelall"] += 1
    elif "deferred: write budget" in line: H[h]["arb_defer"] += 1
    elif " fv " in line and " | bid " in line: H[h]["quote"] += 1
print("hour          quotes/h fills takes rejFunds defer_lines medDeferred medPlanned  priced resting cyc_s burst")
tot = collections.Counter()
for h in sorted(H):
    d = defer[h]
    print(f"{h} {H[h]['quote']:8d} {H[h]['fill']:5d} {H[h]['take']:5d} {H[h]['rej_funds']:8d} {H[h]['defer_lines']:11d} "
          f"{st.median([a for a, b in d]) if d else 0:11.0f} {st.median([b for a, b in d]) if d else 0:10.0f} "
          f"{st.median(priced[h]) if priced[h] else 0:7.0f} {st.median(resting[h]) if resting[h] else 0:7.0f} "
          f"{st.median(cyc[h]) if cyc[h] else 0:5.1f} {H[h]['burst']:5d}")
    tot.update(H[h])
allD = [x for v in defer.values() for x in v]
print("TOTAL", dict(tot))
print("defer lines %d, median deferred %d of %d; mean %.0f of %.0f" % (len(allD), st.median([a for a, b in allD]),
      st.median([b for a, b in allD]), st.mean([a for a, b in allD]), st.mean([b for a, b in allD])))
