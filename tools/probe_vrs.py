import subprocess, glob, collections
subprocess.run("git clone -q --depth 1 --filter=blob:none --sparse https://github.com/ValveSoftware/counter-strike_regional_standings /tmp/v && git -C /tmp/v sparse-checkout set live/2026/details/2026_11_02", shell=True, check=True)
by = collections.defaultdict(collections.Counter)
for f in glob.glob("/tmp/v/live/2026/details/2026_11_02/*.md"):
    t = open(f).read()
    for line in t.split('id="table1"', 1)[-1].split('id="table2"', 1)[0].splitlines():
        c = [x.strip() for x in line.split("|")]
        if len(c) >= 13 and c[1].isdigit() and c[3][:7] in ("2026-06", "2026-10", "2026-11") and c[7] != "-":
            by[c[3]][c[7]] += 1
for d in sorted(by):
    print("E", d, dict(by[d].most_common(6)))
