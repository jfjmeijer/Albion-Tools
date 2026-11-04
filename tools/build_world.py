"""Maakt tools/world.json (zones, kleur, kaartpositie en buren) uit ao-bin-dumps cluster/world.xml.

Gebruik: python3 -I tools/build_world.py <world.xml> tools/world.json
Roads of Avalon (TUNNEL_*), dungeons, eilanden en stadsgebouwen worden overgeslagen.
"""
import json
import sys
import xml.etree.ElementTree as ET

src, dst = sys.argv[1], sys.argv[2]
root = ET.parse(src).getroot()
clusters = {c.get("id"): c for c in root.find("clusters").findall("cluster")}

SKIP = ("DUNGEON", "ISLAND", "EXPEDITION", "ARENA", "HALL_OF_FAME", "HELLDEN", "SMUGGLERSDEN", "TUTORIAL")


def color(t):
    if not t or t.startswith("TUNNEL") or t.startswith("DEBUG") or t == "HIDEOUT" or any(s in t for s in SKIP):
        return None
    if t.startswith("PLAYERCITY") or t == "STARTINGCITY":
        return "c"   # stad
    if t in ("SAFEAREA", "PASSAGE_SAFEAREA", "STARTAREA"):
        return "b"   # blauw
    if "YELLOW" in t:
        return "y"
    if "RED" in t:
        return "r"
    if "BLACK" in t:
        return "k"   # zwart
    return None


zones = {}
for cid, c in clusters.items():
    col = color(c.get("type"))
    if not col:
        continue
    z = {"n": c.get("displayname") or cid, "c": col, "t": c.get("type")}
    wmp = c.get("worldmapposition")
    if wmp:
        x, y = (float(v) for v in wmp.split())
        z["p"] = [round(x, 2), round(y, 2)]
    zones[cid] = z

for cid, z in zones.items():
    nb = []
    for e in clusters[cid].findall("./exits/exit"):
        tid = e.get("targetid", "")
        if "@" not in tid or e.get("targettype") != "Cluster":
            continue
        tgt = tid.split("@", 1)[1]
        if tgt in zones and tgt != cid and tgt not in nb:
            nb.append(tgt)
    z["e"] = nb

# alleen zones die ergens mee verbonden zijn
zones = {k: v for k, v in zones.items() if v["e"]}
# verbindingen moeten twee kanten op werken
for k, v in zones.items():
    v["e"] = [t for t in v["e"] if t in zones]

out = {"source": "ao-bin-dumps cluster/world.xml", "zones": zones}
json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
from collections import Counter
print("zones:", len(zones), Counter(z["c"] for z in zones.values()), "met kaartpositie:", sum(1 for z in zones.values() if "p" in z))
