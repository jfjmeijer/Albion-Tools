import json
import re
import sys
from collections import defaultdict

items_txt, items_json, template_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

TIERWORD = {4: "Adept's", 5: "Expert's", 6: "Master's", 7: "Grandmaster's", 8: "Elder's"}
REFINED_RES = ("METALBAR", "PLANKS", "CLOTH", "LEATHER")      # enchant-relative in gear recipes
RAW_KINDS = ("ORE", "WOOD", "FIBER", "HIDE", "ROCK")
REFINED_KINDS = ("METALBAR", "PLANKS", "CLOTH", "LEATHER", "STONEBLOCK")

# ---------- namen ----------
pat = re.compile(r"^\s*\d+:\s+(\S+)\s*:\s*(.*)$")
names = {}
for line in open(items_txt, encoding="utf-8"):
    m = pat.match(line.rstrip("\n"))
    if m:
        names[m.group(1)] = m.group(2).strip()

# ---------- itemdefinities (alleen top-level, niet de verwijzingen in recepten) ----------
root = json.load(open(items_json, encoding="utf-8"))["items"]
nodes = {}
for key, val in root.items():
    if key.startswith("@"):
        continue
    entries = val if isinstance(val, list) else [val]
    for n in entries:
        if isinstance(n, dict) and "@uniquename" in n:
            nodes.setdefault(n["@uniquename"], n)


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def first_recipe(crq):
    """Eerste (standaard)variant van een recept als lijst [id, aantal, teruggeefbaar]."""
    reqs = as_list(crq)
    if not reqs:
        return None
    out = []
    for c in as_list(reqs[0].get("craftresource")):
        uid = c["@uniquename"]
        lvl = c.get("@enchantmentlevel")
        if lvl and lvl != "0":
            uid = uid + "@" + lvl
        out.append([uid, int(c["@count"]), 0 if c.get("@maxreturnamount") == "0" else 1])
    return out or None


# ---------- gear ----------
gear = re.compile(r"^T([4-8])_(MAIN|2H|OFF|HEAD|ARMOR|SHOES|CAPE|BAG)(_[A-Z0-9_]+)?(?:@([1-4]))?$")
bases = {}
for iid, name in names.items():
    g = gear.match(iid)
    if not g:
        continue
    tier, kind, rest, ench = int(g.group(1)), g.group(2), g.group(3) or "", int(g.group(4) or 0)
    if "TOOL" in rest:
        continue
    base = iid.split("_", 1)[1].split("@")[0]
    b = bases.setdefault(base, {"m": [0] * 7, "names": {}, "kind": kind, "rest": rest})
    b["m"][tier - 2] |= 1 << ench
    b["names"][tier] = name


def category(kind, rest):
    if kind in ("MAIN", "2H"):
        return "w"
    if kind in ("HEAD", "ARMOR", "SHOES"):
        return "g" if "GATHERER" in rest else "a"
    if kind == "OFF":
        return "o"
    if kind == "CAPE":
        return "c"
    return "b"


def name_fields(nm):
    rests, ok = set(), True
    for t, n in nm.items():
        w = TIERWORD[t]
        if n.startswith(w + " "):
            rests.add(n[len(w) + 1:])
        else:
            ok = False
    if ok and len(rests) == 1:
        return {"n": rests.pop(), "p": 1}
    return {"nn": {str(t): n for t, n in sorted(nm.items())}}


def expand(tpl, tier, e):
    out = []
    for k, key, n, r in tpl:
        if k == "R":
            cid = "T%d_%s%s" % (tier, key, ("_LEVEL%d@%d" % (e, e)) if e else "")
        elif k == "A":
            cid = "T%d_%s" % (tier, key)
        elif k == "E":
            cid = "T%d_%s%s" % (tier, key, ("@%d" % e) if e else "")
        elif k == "S":
            cid = "%s_T%d" % (key, tier)
        else:
            cid = key
        out.append([cid, n, r])
    return out


def actual_recipes(base):
    """(tier, enchant) -> recept voor een gear-base, uit de game-data."""
    res = {}
    for tier in range(4, 9):
        node = nodes.get("T%d_%s" % (tier, base))
        if not node:
            continue
        res[(tier, 0)] = first_recipe(node.get("craftingrequirements"))
        ens = node.get("enchantments")
        for e in as_list(ens.get("enchantment") if isinstance(ens, dict) else None):
            res[(tier, int(e["@enchantmentlevel"]))] = first_recipe(e.get("craftingrequirements"))
    return res


def make_template(rec, tier, actual):
    tpl = []
    nxt_ids = {c[0] for c in (actual.get((tier, 1)) or [])}
    for cid, n, r in rec:
        m = re.match(r"^T%d_(.+)$" % tier, cid)
        if m and m.group(1) in REFINED_RES:
            tpl.append(["R", m.group(1), n, r])
        elif m and (cid + "@1") in nxt_ids:
            tpl.append(["E", m.group(1), n, r])
        elif m:
            tpl.append(["A", m.group(1), n, r])
        else:
            m2 = re.match(r"^(.+)_T%d$" % tier, cid)
            tpl.append(["S", m2.group(1), n, r] if m2 else ["X", cid, n, r])
    return tpl


def norm(rec):
    return sorted((c[0], c[1], c[2]) for c in rec) if rec else None


out = []
stats = {"bases": 0, "with_recipe": 0, "overrides": 0, "no_recipe_combos": 0}
for base, b in bases.items():
    rec = {"b": base, "c": category(b["kind"], b["rest"]), "m": b["m"]}
    rec.update(name_fields(b["names"]))
    actual = actual_recipes(base)
    ref_key = next((k for k in sorted(actual) if k[1] == 0 and actual[k]), None)
    if ref_key:
        tpl = make_template(actual[ref_key], ref_key[0], actual)
        ov = {}
        for ti, mask in enumerate(b["m"]):
            tier = ti + 2
            for e in range(5):
                if not (mask >> e) & 1:
                    continue
                want = actual.get((tier, e))
                got = expand(tpl, tier, e)
                if want is None:
                    ov["%d_%d" % (tier, e)] = None
                    stats["no_recipe_combos"] += 1
                elif norm(want) != norm(got):
                    ov["%d_%d" % (tier, e)] = want
                    stats["overrides"] += 1
        rec["rc"] = tpl
        if any(c[0] in ("A", "E") and c[1].startswith("ARTEFACT_") and not c[1].startswith("ARTEFACT_TOKEN") for c in tpl):
            rec["af"] = 1
        if ov:
            rec["ov"] = ov
        stats["with_recipe"] += 1
    stats["bases"] += 1
    out.append(rec)

# ---------- grondstoffen en gerefinde materialen ----------
res_re = re.compile(r"^T([2-8])_(ORE|WOOD|FIBER|HIDE|ROCK|METALBAR|PLANKS|CLOTH|LEATHER|STONEBLOCK)(?:_LEVEL([1-4])@([1-4]))?$")
res = {}
for iid in names:
    m = res_re.match(iid)
    if not m:
        continue
    tier, kind, e = int(m.group(1)), m.group(2), int(m.group(4) or 0)
    r = res.setdefault(kind, {"m": [0] * 7, "nn": {}})
    r["m"][tier - 2] |= 1 << e
    if e == 0:
        r["nn"][str(tier)] = names[iid]

for kind, r in res.items():
    rec = {"b": kind, "c": "r" if kind in RAW_KINDS else "m", "f": "L", "m": r["m"], "nn": r["nn"]}
    if kind in REFINED_KINDS:
        rx = {}
        for ti, mask in enumerate(r["m"]):
            tier = ti + 2
            for e in range(5):
                if not (mask >> e) & 1:
                    continue
                uid = "T%d_%s%s" % (tier, kind, ("_LEVEL%d" % e) if e else "")
                node = nodes.get(uid)
                recipe = first_recipe(node.get("craftingrequirements")) if node else None
                if recipe:
                    rx["%d_%d" % (tier, e)] = recipe
        if rx:
            rec["rx"] = rx
    out.append(rec)

# ---------- craftcategorie (voor de bonusstad) ----------
REFINE_CC = {"METALBAR": "ore", "PLANKS": "wood", "CLOTH": "fiber", "LEATHER": "hide", "STONEBLOCK": "rock"}
for rec in out:
    if rec.get("f") == "L":
        if rec["b"] in REFINE_CC:
            rec["cc"] = REFINE_CC[rec["b"]]
        continue
    for tier in range(2, 9):
        node = nodes.get("T%d_%s" % (tier, rec["b"]))
        if node and node.get("@craftingcategory"):
            rec["cc"] = node["@craftingcategory"]
            break
print("bases with crafting category:", sum(1 for r in out if r.get("cc")), "of", len(out))

# ---------- spec-tree (welke crafter-spec het item gebruikt) ----------
for rec in out:
    if rec.get("f") == "L":
        continue
    st = None
    for tier in range(4, 9):
        node = nodes.get("T%d_%s" % (tier, rec["b"]))
        if not node:
            continue
        sub, cc = node.get("@shopsubcategory1"), node.get("@craftingcategory")
        if sub and sub != "other":
            st = ("gather_" + sub) if cc == "gatherergear" else sub
            break
    if not st:
        st = {"c": "capes", "b": "bags"}.get(rec["c"])
    if st in ("accessoires_capes_capes",):
        st = "capes"
    if st == "satchels":
        st = "bags"
    if st:
        rec["st"] = st
print("bases with a spec tree:", sum(1 for r in out if r.get("st")), sorted({r["st"] for r in out if r.get("st")}))

# ---------- gewichten per tier (kg), voor winst per kg ----------
wmiss = 0
for rec in out:
    wt = []
    for tier in range(2, 9):
        node = nodes.get("T%d_%s" % (tier, rec["b"]))
        try:
            wt.append(round(float(node["@weight"]), 3) if node and node.get("@weight") else 0)
        except ValueError:
            wt.append(0)
    if any(rec["m"][i] and not wt[i] for i in range(7)):
        wmiss += 1
    rec["wt"] = wt
print("bases with a missing weight:", wmiss)

order = {"w": 0, "a": 1, "o": 2, "c": 3, "b": 4, "g": 5, "r": 6, "m": 7}
out.sort(key=lambda r: (order[r["c"]], r.get("n") or list(r["nn"].values())[0]))
payload = json.dumps(out, separators=(",", ":"), ensure_ascii=True)

tpl_html = open(template_path, encoding="utf-8").read()
if "__ITEMDATA__" not in tpl_html:
    raise SystemExit("placeholder missing")
import os
bonus_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "craftingbonus.json")
bonus = json.load(open(bonus_path, encoding="utf-8"))
if "__CRAFTBONUS__" not in tpl_html:
    raise SystemExit("craftbonus placeholder missing")
world = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "world.json"), encoding="utf-8"))
if "__WORLD__" not in tpl_html:
    raise SystemExit("world placeholder missing")
html = (tpl_html.replace("__ITEMDATA__", payload)
        .replace("__CRAFTBONUS__", json.dumps(bonus, separators=(",", ":"), ensure_ascii=True))
        .replace("__WORLD__", json.dumps(world, separators=(",", ":"), ensure_ascii=True)))
open(out_path, "w", encoding="utf-8").write(html)

cats = defaultdict(int)
for r in out:
    cats[r["c"]] += 1
print("stats:", stats)
print("categories:", dict(cats))
print("refined recipes:", {r["b"]: len(r.get("rx", {})) for r in out if r["c"] == "m"})
print("html bytes:", len(html.encode("utf-8")))
