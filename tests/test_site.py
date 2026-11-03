import datetime
import json
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

SRC, TEST_COPY, SHOT_DIR = sys.argv[1], sys.argv[2], sys.argv[3]
html = open(SRC, encoding="utf-8").read()
assert "const MIN_GAP_MS = 1100;" in html
open(TEST_COPY, "w", encoding="utf-8").write(html.replace("const MIN_GAP_MS = 1100;", "const MIN_GAP_MS = 5;"))
ZERO = "0001-01-01T00:00:00"


def iso(minutes_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


SPECIAL = {
    "T5_MAIN_SWORD": {"Lymhurst": dict(sell=100000, buy=90000), "Martlock": dict(sell=140000, buy=120000),
                      "Black Market": dict(sell=0, buy=130000)},
    "T4_ORE": {"Lymhurst": dict(sell=200, buy=0), "Martlock": dict(sell=0, buy=320), "Black Market": dict(sell=0, buy=99999)},
}
DEFAULT_CITY = dict(sell=150000, buy=100000)
DEFAULT_BM = dict(sell=0, buy=100000)
HIST = {("T5_MAIN_SWORD", "Lymhurst"): 14, ("T5_MAIN_SWORD", "Martlock"): 4, ("T5_MAIN_SWORD", "Black Market"): None,
        ("T4_ORE", "Lymhurst"): 3000, ("T4_ORE", "Martlock"): 2500}


def record(item, loc, q):
    spec = SPECIAL.get(item, {}).get(loc) or (DEFAULT_BM if loc == "Black Market" else DEFAULT_CITY)
    s, b = spec["sell"], spec["buy"]
    return {"item_id": item, "city": loc, "quality": q, "sell_price_min": s, "sell_price_min_date": iso(5) if s else ZERO,
            "sell_price_max": s, "sell_price_max_date": ZERO, "buy_price_min": 0, "buy_price_min_date": ZERO,
            "buy_price_max": b, "buy_price_max_date": iso(5) if b else ZERO}


def parse(url, marker):
    pr = urllib.parse.urlparse(url)
    ids = urllib.parse.unquote(pr.path.split(marker)[1]).split(",")
    qs = urllib.parse.parse_qs(pr.query)
    return ids, qs["locations"][0].split(","), [int(x) for x in qs["qualities"][0].split(",")]


def prices(route):
    ids, locs, qs = parse(route.request.url, "/prices/")
    route.fulfill(status=200, body=json.dumps([record(i, l, q) for i in ids for l in locs for q in qs]),
                  headers={"access-control-allow-origin": "*", "content-type": "application/json"})


def history(route):
    ids, locs, qs = parse(route.request.url, "/history/")
    now = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in ids:
        for l in locs:
            n = HIST.get((i, l), 20)
            if n is None:
                continue
            out.append({"location": l, "item_id": i, "quality": 1, "data": [
                {"item_count": n, "avg_price": 1000, "timestamp": (now - datetime.timedelta(days=k)).strftime("%Y-%m-%dT%H:%M:%S")} for k in range(1, 8)]})
    route.fulfill(status=200, body=json.dumps(out), headers={"access-control-allow-origin": "*", "content-type": "application/json"})


failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def rows(page, sel="#tbody tr"):
    data = page.eval_on_selector_all(sel, "rs => rs.map(r => Array.from(r.children).map(c => c.innerText.replace(/\\s+/g,' ').trim()))")
    for r in data:
        if len(r) > 1:
            r[1] = re.sub(r"\s*→\s*", " → ", r[1])
        if len(r) > 6:
            r[6] = re.sub(r"\s*/\s*", " / ", r[6])
    return data


DONE = "document.getElementById('status').textContent.startsWith('Klaar') && !document.getElementById('scanBtn').disabled"

MOCK_CLAUDE = """
(() => {
  const store = {}; const colL = []; const docL = {};
  const fire = () => { for (const f of colL) f(); for (const k in docL) for (const f of docL[k]) f(); };
  const db = {
    collection(path) { return { onSnapshot(next) {
      const f = () => { const docs = Object.keys(store).filter(k => k.startsWith(path + '/')).map(k => ({id: k.split('/')[1], exists: true, data: () => store[k]}));
        next({docs, size: docs.length, empty: !docs.length, metadata: {fromCache: false, hasPendingWrites: false}, docChanges: () => []}); };
      colL.push(f); setTimeout(f, 20); return () => {}; } }; },
    doc(path) { return {
      set: async d => { window.__writes.push(path); store[path] = JSON.parse(JSON.stringify(d)); setTimeout(fire, 5); },
      delete: async () => { window.__writes.push('del:' + path); delete store[path]; setTimeout(fire, 5); },
      onSnapshot(next) { const f = () => next({exists: path in store, data: () => store[path], metadata: {fromCache: false}});
        (docL[path] = docL[path] || []).push(f); setTimeout(f, 20); return () => {}; } }; }
  };
  window.__writes = []; window.__store = store; window.__saved = null;
  window.claude = { use: async n => n === 'db' ? db : n === 'downloads' ? { save: async o => { window.__saved = o; } } : null };
})();
"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950}, accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), prices)
    page.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), history)
    page.goto("file://" + TEST_COPY)
    page.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")

    check(page.is_visible("#viewMarkt") and not page.is_visible("#viewLog"), "start op tab Markt")
    check(not page.is_visible("#hostBanner"), "geen claude.ai-melding in het losse bestand")

    # ---------- Routes ----------
    page.click("#modeRoutes")
    chips = page.eval_on_selector_all("#sellCities button", "bs => bs.map(b => b.textContent)")
    check("Black Market" in chips, "routes: Black Market staat tussen de verkoopplekken")
    check(page.get_attribute("#thead th:nth-child(8)", "aria-sort") == "descending", "routes: standaard gesorteerd op Winst/dag")
    page.click("#scanBtn")
    page.wait_for_function(DONE, timeout=90000)
    sw = [r for r in rows(page) if "Broadsword" in r[0]]
    print("route-rij:", sw)
    check(len(sw) == 1 and sw[0][1] == "Lymhurst → Martlock", "routes: beste route op winst/dag is Lymhurst → Martlock (niet BM met hogere winst/stuk)")
    check(sw and sw[0][7] == "60.800", "routes: winst/dag 15.200 × 4 = 60.800 (was %s)" % (sw[0][7] if sw else None))
    check(sw and sw[0][8] == "2.000", "routes: winst/kg 15.200 / 7,6 kg = 2.000 (was %s)" % (sw[0][8] if sw else None))
    page.check("#showAll")
    n_all = len([r for r in rows(page) if "Broadsword" in r[0]])
    check(n_all >= 2, "routes: alle routes per item toont meerdere routes (%d)" % n_all)
    page.uncheck("#showAll")
    # grondstoffen: nooit naar de Black Market
    page.locator("#cats button", has_text="Ruwe").first.click()
    page.fill("#minProfit", "50")
    page.click("#scanBtn")
    page.wait_for_function(DONE, timeout=90000)
    page.check("#showAll")
    ore = [r for r in rows(page) if "Ore" in r[0] and r[0].startswith("T4")]
    print("ore-rijen:", ore[:3])
    check(ore and all("Black Market" not in r[1] for r in ore), "routes: grondstoffen gaan nooit naar de Black Market")
    check(any(r[1] == "Lymhurst → Martlock" for r in ore), "routes: ore-route Lymhurst → Martlock gevonden")
    page.uncheck("#showAll")
    page.locator("#viewMarkt .results").screenshot(path=SHOT_DIR + "/routes.png")

    # Log-knop vanuit Markt
    page.fill("#q", "broadsword")
    page.locator("#tbody tr", has_text="Broadsword").first.locator(".logbtn").click()
    check(page.is_visible("#viewLog") and page.url.endswith("#logboek"), "Log-knop opent het logboek")
    check("Lymhurst → Martlock" in page.input_value("#lgNote") and page.input_value("#lgAmount") == "15200", "logboek is ingevuld vanuit de kans")
    check(page.input_value("#lgCat") == "Handelsroute", "actie = Handelsroute")
    page.fill("#lgQty", "3")
    check("+45.600" in page.inner_text("#lgPreview"), "voorbeeld toont totaal 3 × 15.200: " + page.inner_text("#lgPreview"))
    page.click("#lgAdd")
    check(len(rows(page, "#lgBody tr")) == 1, "regel toegevoegd")

    # ---------- Logboek handmatig ----------
    page.fill("#lgAmount", "250k"); page.fill("#lgQty", "2")
    check("+500.000" in page.inner_text("#lgPreview"), "250k × 2 = +500.000")
    page.click("#lgAdd")
    page.click("#lgTypeExp")
    page.select_option("#lgCat", "Gear / set")
    page.fill("#lgAmount", "1,2m"); page.fill("#lgNote", "transport set")
    page.click("#lgAdd")
    lr = rows(page, "#lgBody tr")
    print("logregels:", lr)
    check(len(lr) == 3 and lr[0][5] == "−1.200.000", "uitgave −1.200.000 bovenaan")
    tiles = page.inner_text("#lgTiles")
    exp_worth = 7000000 + 45600 + 500000 - 1200000
    print("tiles:", tiles.replace("\n", " | "))
    check("6,3m" in tiles, "vermogen = %d ≈ 6,3m" % exp_worth)
    check(page.locator("#chLine path").count() >= 1 and page.locator("#chBars rect").count() >= 2 and page.locator("#chCats rect").count() >= 2, "drie grafieken getekend")
    box = page.locator("#chLine svg").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.8, box["y"] + box["height"] / 2)
    check(page.is_visible("#chLine .tip"), "tooltip op de vermogenslijn")
    page.fill("#lgGoal", "10m"); page.press("#lgGoal", "Tab")
    check("van 10m" in page.inner_text("#lgTiles"), "doel aanpasbaar (10m)")
    page.screenshot(path=SHOT_DIR + "/logboek.png", full_page=True)

    # periodefilter
    page.click("#lgPeriods button[data-p='7']")
    check(len(rows(page, "#lgBody tr")) == 3, "periode 7 dagen toont de regels van vandaag")

    # verwijderen (twee klikken)
    page.locator("#lgBody tr").first.locator(".del").click()
    check(len(rows(page, "#lgBody tr")) == 3, "eerste klik verwijdert nog niet")
    page.locator("#lgBody tr").first.locator(".del").click()
    check(len(rows(page, "#lgBody tr")) == 2, "tweede klik verwijdert")

    # export
    with page.expect_download() as dl:
        page.click("#lgExport")
    path = dl.value.path()
    exp = json.load(open(path, encoding="utf-8"))
    check(len(exp["entries"]) == 2 and exp["goal"] == 10000000, "JSON-export bevat 2 regels en het doel")
    with page.expect_download() as dl2:
        page.click("#lgExportCsv")
    csv = open(dl2.value.path(), encoding="utf-8-sig").read()
    check(csv.startswith("Datum;Soort;Actie") and csv.count("\n") == 2, "CSV-export")

    # bewaard na herladen
    page.reload()
    page.wait_for_timeout(200)
    check(page.is_visible("#viewLog") and len(rows(page, "#lgBody tr")) == 2, "logboek en tab bewaard na herladen")

    # import (nieuw + dubbel)
    imp = {"entries": exp["entries"] + [{"id": "extra1", "t": exp["entries"][0]["t"] - 86400000 * 3, "type": "win", "cat": "Craften", "note": "import", "qty": 1, "amount": 800000}]}
    open("/tmp/imp.json", "w").write(json.dumps(imp))
    page.set_input_files("#lgImport", "/tmp/imp.json")
    page.wait_for_timeout(300)
    check(len(rows(page, "#lgBody tr")) == 3 and "1 regel geïmporteerd" in page.inner_text("#lgIoMsg"), "import voegt alleen nieuwe regels toe: " + page.inner_text("#lgIoMsg"))
    check(not errors, "geen JS-fouten: %s" % errors)

    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(300)
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2")
    check(not overflow, "logboek: geen horizontale overflow op mobiel")
    page.screenshot(path=SHOT_DIR + "/logboek-mobile.png", full_page=False)

    # ---------- als claude.ai-site (gesimuleerde runtime) ----------
    ctx2 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950})
    ctx2.add_init_script(MOCK_CLAUDE)
    p2 = ctx2.new_page()
    err2 = []
    p2.on("pageerror", lambda e: err2.append(str(e)))
    p2.route(re.compile(r"albion-online-data\.com/"), lambda r: r.abort())
    p2.goto("file://" + TEST_COPY)
    p2.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    p2.wait_for_timeout(300)
    check(p2.is_visible("#hostBanner"), "claude.ai: melding dat live prijzen hier niet werken")
    p2.click("#dlSelfBtn")
    p2.wait_for_function("window.__saved !== null", timeout=5000)
    saved = p2.evaluate("window.__saved")
    check(saved["filename"] == "albion-tools.html" and "Albion <span>Tools</span>" in saved["data"] and "const BASES" in saved["data"], "downloadversie bevat de hele site")
    p2.click("#tabLogboek")
    p2.wait_for_timeout(200)
    check(not p2.is_visible("#hostBanner"), "melding alleen op Markt")
    check("online" in p2.inner_text("#lgSync"), "logboek gebruikt online opslag: " + p2.inner_text("#lgSync"))
    p2.fill("#lgAmount", "100k"); p2.click("#lgAdd")
    p2.wait_for_timeout(200)
    writes = p2.evaluate("window.__writes")
    month = datetime.datetime.now().strftime("%Y-%m")
    check(("logmonths/" + month) in writes, "regel opgeslagen in logmonths/%s (writes: %s)" % (month, writes))
    check(len(rows(p2, "#lgBody tr")) == 1, "regel zichtbaar na snapshot")
    p2.fill("#lgStart", "15m"); p2.press("#lgStart", "Tab")
    p2.wait_for_timeout(1000)
    check("meta/settings" in p2.evaluate("window.__writes"), "startkapitaal online opgeslagen")
    check("15,1m" in p2.inner_text("#lgTiles"), "vermogen 15m + 100k = 15,1m")
    p2.locator("#lgBody tr").first.locator(".del").click()
    p2.locator("#lgBody tr").first.locator(".del").click()
    p2.wait_for_timeout(200)
    check(("del:logmonths/" + month) in p2.evaluate("window.__writes"), "lege maand wordt verwijderd")
    check(not err2, "geen JS-fouten in claude.ai-modus: %s" % err2)
    # ---------- mandje en afvinklijst ----------
    ctx3 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950})
    p3 = ctx3.new_page()
    err3 = []
    p3.on("pageerror", lambda e: err3.append(str(e)))
    p3.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), prices)
    p3.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), history)
    p3.goto("file://" + TEST_COPY)
    p3.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    check(not p3.is_visible("#cartBar"), "mandjebalk verborgen zolang het mandje leeg is")
    p3.click("#scanBtn"); p3.wait_for_function(DONE, timeout=90000)
    row = p3.locator("#tbody tr", has_text="Broadsword").first
    row.locator(".cartbtn").click()
    check(row.locator(".cartbtn").inner_text() == "✓ In mandje", "knop wordt ‘In mandje’")
    check(p3.is_visible("#cartBar") and "1 item" in p3.inner_text("#cartBar"), "mandjebalk toont 1 item")
    check(p3.inner_text("#tabMandje") == "Mandje (1)", "tab toont aantal: " + p3.inner_text("#tabMandje"))
    # craft-kans erbij
    SPECIAL["T5_MAIN_SWORD@1"] = {"Black Market": dict(sell=0, buy=400000)}
    SPECIAL["T5_METALBAR_LEVEL1@1"] = {"Lymhurst": dict(sell=20000, buy=0)}
    SPECIAL["T5_LEATHER_LEVEL1@1"] = {"Martlock": dict(sell=10000, buy=0)}
    p3.click("#modeCraft")
    for t in ["T4", "T6"]:
        p3.locator("#tiers button", has_text=t).first.click()
    p3.click("#scanBtn"); p3.wait_for_function(DONE, timeout=90000)
    crow = [r for r in rows(p3) if r[0].startswith("T5.1") and "Broadsword" in r[0]]
    print("craft-rij:", crow)
    rr_sw = 1 - 1 / 1.33
    craft_profit = 400000 * 0.96 - 400000 * (1 - rr_sw)
    fmt_p = "+" + "{:,}".format(round(craft_profit)).replace(",", ".")
    check(crow and crow[0][4] == fmt_p, "craft T5.1 broadsword %s met RRR 24,8%% (was %s)" % (fmt_p, crow[0][4] if crow else None))
    check(crow and crow[0][1] == "craft Lymhurst → Black Market", "craftstad Lymhurst getoond (was %s)" % (crow[0][1] if crow else None))
    p3.locator("#tbody tr", has_text="Expert's Broadsword").filter(has=p3.locator("span.tier", has_text="T5.1")).first.locator(".cartbtn").click()
    p3.click("#cartBarBtn")
    check(p3.is_visible("#viewCart") and p3.url.endswith("#mandje"), "naar mandje-tab")
    crt = rows(p3, "#cartRoot tbody tr")
    print("mandje:", crt)
    check(len(crt) == 2, "2 items in het mandje")
    p3.locator("#cartRoot tbody tr", has_text="Craften").locator("input.qty").fill("3")
    tot = p3.inner_text("#cartTotals")
    batch = 24800 + 3 * craft_profit
    k = round(batch / 1000)
    check(("+%dk" % k) in tot, "verwachte winst 24.800 + 3 × craft = %d: %s" % (batch, tot.replace("\n", " | ")))
    p3.click("#cartGo")
    lst = p3.inner_text("#cartRoot")
    print("lijst:", lst.replace("\n", " | ")[:900])
    check("1× T5 Expert's Broadsword" in lst, "kopen: het flip-zwaard")
    import math
    nbar = math.ceil(48 * (1 - rr_sw) - 1e-9)
    check(("%d× Titanium Steel Bar .1" % nbar) in lst and "zonder terugkomst 48" in lst, "kopen: staven met RRR (%d, zonder 48)" % nbar)
    check("craften in Lymhurst" in lst, "craftstap noemt de craftstad")
    nlea = math.ceil(24 * (1 - rr_sw) - 1e-9)
    check(("%d× " % nlea) in lst and "zonder terugkomst 24" in lst, "kopen: leer met RRR (%d, zonder 24)" % nlea)
    check("3× T5.1 Expert's Broadsword craften in Lymhurst" in lst, "craftstap")
    check("BLACK MARKET (CAERLEON)" in lst.upper(), "verkopen gegroepeerd op Black Market")
    n_chk = p3.locator("#cartRoot .chk input").count()
    for i in range(n_chk):
        p3.locator("#cartRoot .chk input").nth(i).check()
    check(("%d van %d gedaan" % (n_chk, n_chk)) in p3.inner_text("#cartProgText"), "voortgang: alles afgevinkt")
    p3.reload(); p3.wait_for_timeout(200)
    check(p3.is_visible("#viewCart") and p3.locator("#cartRoot .chk input:checked").count() == n_chk, "lijst en vinkjes bewaard na herladen")
    p3.locator("#cartRoot .chk input").first.uncheck()
    p3.screenshot(path=SHOT_DIR + "/mandje-lijst.png", full_page=True)
    p3.click("#cartFinish")
    check(p3.is_visible("#viewLog") and p3.input_value("#lgAmount") == str(round(batch)) and "Batch: 4 stuks" in p3.input_value("#lgNote"), "batch afronden vult het logboek in")
    p3.click("#tabMandje")
    p3.click("text=← Terug naar mandje")
    p3.screenshot(path=SHOT_DIR + "/mandje.png", full_page=True)
    p3.locator("#cartRoot tbody tr").first.locator(".del").click()
    check(len(rows(p3, "#cartRoot tbody tr")) == 1, "item uit mandje verwijderen")
    check(not err3, "geen JS-fouten in mandje: %s" % err3)

    # ---------- laadplanner, buy orders en orders-tab ----------
    for k in ["T5_MAIN_SWORD@1", "T5_METALBAR_LEVEL1@1", "T5_LEATHER_LEVEL1@1"]:
        SPECIAL.pop(k, None)
    ctx4 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950})
    p4 = ctx4.new_page()
    err4 = []
    p4.on("pageerror", lambda e: err4.append(str(e)))
    p4.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), prices)
    p4.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), history)
    p4.goto("file://" + TEST_COPY)
    p4.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    p4.click("#scanBtn"); p4.wait_for_function(DONE, timeout=90000)
    p4.click("#planner summary")
    p4.fill("#plBudget", "250k"); p4.press("#plBudget", "Tab")
    out = p4.inner_text("#plOut")
    print("planner:", out.replace("\n", " | ")[:300])
    check("2 stuks" in out and "Lymhurst → Black Market" in out, "planner: budget 250k → 2 zwaarden (100k per stuk)")
    p4.fill("#plKg", "10")
    check("1 stuks" in p4.inner_text("#plOut"), "planner: 10 kg → 1 zwaard (7,6 kg)")
    p4.fill("#plKg", "0"); p4.fill("#plBudget", "10m"); p4.press("#plBudget", "Tab")
    check("7 stuks" in p4.inner_text("#plOut"), "planner: begrensd op 50% van 14 per dag = 7")
    p4.click("#plToCart")
    check(p4.inner_text("#tabMandje") == "Mandje (1)" and p4.evaluate("CART.items[0].qty") == 7, "planner zet lading in mandje (7 stuks)")
    p4.locator("#viewMarkt .results").screenshot(path=SHOT_DIR + "/planner.png")

    # buy orders
    HIST[("T5_MAIN_SWORD", "Lymhurst")] = 40
    p4.click("#modeOrders")
    check(not p4.is_visible("#buyMethod") and not p4.is_visible("#sellMethod"), "buy orders: koop/verkoopmethode verborgen (vast)")
    p4.click("#scanBtn"); p4.wait_for_function(DONE, timeout=90000)
    p4.fill("#q", "expert's broadsword")
    sw = [r for r in rows(p4) if "Broadsword" in r[0] and r[0].startswith("T5E")]
    print("order-rij:", sw)
    exp_o = 149999 * (1 - 0.04 - 0.025) - 90001 * 1.025
    fo = "+" + "{:,}".format(round(exp_o)).replace(",", ".")
    check(sw and sw[0][1].startswith("Lymhurst → ") and sw[0][2].startswith("90.001") and sw[0][3].startswith("149.999"), "buy order +1 en sell order −1")
    check(sw and sw[0][4] == fo, "buy-order winst %s (was %s)" % (fo, sw[0][4] if sw else None))
    p4.locator("#tbody tr").filter(has=p4.locator("span.tier", has_text=re.compile(r"^T5$"))).first.locator(".ordbtn").click()
    check(p4.inner_text("#tabOrders") == "Orders (1)", "order gevolgd: " + p4.inner_text("#tabOrders"))
    p4.click("#tabOrders")
    st = p4.inner_text("#ordRoot")
    check("Jij bent het hoogst" in st and "Lymhurst" in st, "orders-tab: jij bent het hoogst")
    SPECIAL["T5_MAIN_SWORD"]["Lymhurst"] = dict(sell=100000, buy=95000)
    p4.click("#ordCheck")
    p4.wait_for_function("document.getElementById('ordMsg') && document.getElementById('ordMsg').textContent.startsWith('Gecheckt')", timeout=20000)
    st = p4.inner_text("#ordRoot")
    print("orders na check:", st.replace("\n", " | ")[:400])
    check("Overboden: hoogste is nu 95.000" in st and "1 van je 1 orders zijn overboden" in st, "check nu: overboden gemeld")
    p4.click("text=Op 95.001 zetten")
    check(p4.locator("#ordRoot input.qty").first.input_value() == "95001", "prijs aangepast naar 95.001")
    p4.screenshot(path=SHOT_DIR + "/orders.png", full_page=True)
    p4.click("#ordRoot button.logbtn:text-is(\"Gevuld\")")
    check("Gevuld: klaar om te verkopen" in p4.inner_text("#ordRoot") and p4.inner_text("#tabOrders") == "Orders", "order als gevuld gemarkeerd")
    p4.click("#ordRoot .cartbtn")
    p4.click("#tabMandje"); p4.click("#cartGo")
    lst = p4.inner_text("#cartRoot")
    check("sell order plaatsen voor minstens 149.999" in lst, "gevulde order staat bij verkopen")
    check("95.001" not in lst.split("Verkopen")[0], "gevulde order hoeft niet meer gekocht te worden")
    check(not err4, "geen JS-fouten bij planner/orders: %s" % err4)

    browser.close()

print("\nFAILURES:", len(failures))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
