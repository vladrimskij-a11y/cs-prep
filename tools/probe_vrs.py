import subprocess, os, glob
subprocess.run("git clone -q --depth 1 --filter=blob:none --sparse https://github.com/ValveSoftware/counter-strike_regional_standings /tmp/v && git -C /tmp/v sparse-checkout set live/2026/details/2026_11_02", shell=True, check=True)
fs = sorted(glob.glob("/tmp/v/live/2026/details/2026_11_02/*.md"))
print("FILES", len(fs))
for f in fs:
    t = open(f).read()
    if "Cologne" in t:
        print("FILE", os.path.basename(f))
        lines = t.split("\n")
        hdr = [l for l in lines if l.startswith("|") ][:3]
        for l in hdr: print("H", l[:300])
        for l in lines:
            if "Cologne" in l: print("L", l[:300])
        break
