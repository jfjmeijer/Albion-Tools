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
HIST_PRICE = {}
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
                {"item_count": n, "avg_price": HIST_PRICE.get((i, l), 1000), "timestamp": (now - datetime.timedelta(days=k)).strftime("%Y-%m-%dT%H:%M:%S")} for k in range(1, 8)]})
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

    # ---------- eigen data van de pc (hulpprogramma, nagebootst) ----------
    HIST[("T5_MAIN_SWORD", "Lymhurst")] = 14
    SPECIAL["T5_MAIN_SWORD"]["Lymhurst"] = dict(sell=100000, buy=90000)
    now_iso = iso(0)
    LOCAL_RECS = [{"item_id": "T5_MAIN_SWORD", "city": "Black Market", "quality": 1, "sell_price_min": 0, "sell_price_min_date": ZERO,
                   "sell_amount": 0, "buy_price_max": 135000, "buy_price_max_date": now_iso, "buy_amount": 3, "source": "local"}]
    LOCAL_HIST = []

    def local(route):
        url = route.request.url
        if "/status" in url:
            body = {"ok": True, "orders": 3, "histories": len(LOCAL_HIST), "lastUpload": now_iso + "Z", "unknownLocations": {}}
        elif "/history" in url:
            body = LOCAL_HIST
        else:
            body = LOCAL_RECS
        route.fulfill(status=200, body=json.dumps(body), headers={"access-control-allow-origin": "*", "content-type": "application/json"})
    ctx5 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950})
    p5 = ctx5.new_page()
    err5 = []
    p5.on("pageerror", lambda e: err5.append(str(e)))
    p5.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), prices)
    p5.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), history)
    p5.goto("file://" + TEST_COPY)
    p5.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    check("Uit" in p5.inner_text("#localStatus"), "eigen data staat standaard uit")
    p5.check("#localOn")
    p5.wait_for_function("document.getElementById('localStatus').textContent.startsWith('Niet verbonden')", timeout=10000)
    check(True, "zonder hulpprogramma: ‘Niet verbonden’")
    p5.route(re.compile(r"127\.0\.0\.1:47800/"), local)
    p5.uncheck("#localOn"); p5.check("#localOn")
    p5.wait_for_function("document.getElementById('localStatus').textContent.startsWith('Verbonden')", timeout=10000)
    check("3 orders" in p5.inner_text("#localStatus"), "status: verbonden met 3 orders")
    p5.click("#scanBtn"); p5.wait_for_function(DONE, timeout=90000)
    sw = [r for r in rows(p5) if "Broadsword" in r[0]]
    print("met eigen data:", sw)
    check(sw and sw[0][3].startswith("135.000") and "3 st" in sw[0][3], "eigen (nieuwere) BM-prijs 135.000 met aantal 3 wint van openbare 130.000")
    check(sw and sw[0][4] == "+29.600", "winst met eigen prijs: +29.600 (was %s)" % (sw[0][4] if sw else None))
    p5.click("#planner summary"); p5.fill("#plBudget", "10m"); p5.press("#plBudget", "Tab")
    check("3 stuks" in p5.inner_text("#plOut"), "planner begrenst op de 3 stuks die de BM vraagt")
    check(not err5, "geen JS-fouten met eigen data: %s" % err5)

    # ---------- winst met de gemiddelde verkoopprijs ----------
    HIST_PRICE[("T5_MAIN_SWORD", "Martlock")] = 160000
    p5.uncheck("#localOn")
    p5.click("#modeCity")
    p5.click("#scanBtn"); p5.wait_for_function(DONE, timeout=90000)
    p5.fill("#q", "expert's broadsword")
    before = [r for r in rows(p5) if r[0].startswith("T5E")]
    p5.check("#useAvg")
    sw = [r for r in rows(p5) if r[0].startswith("T5E")]
    print("gem. prijs:", before[:1], sw[:1])
    check(sw and sw[0][1] == "Lymhurst → Martlock" and sw[0][3].startswith("160.000") and "gem. 7 dagen" in sw[0][3], "verkoopprijs = gemiddelde van 7 dagen (160.000)")
    check(sw and sw[0][4] == "+53.600", "winst met gemiddelde: 160.000 × 0,96 − 100.000 = +53.600 (was %s)" % (sw[0][4] if sw else None))
    p5.uncheck("#useAvg")
    sw = [r for r in rows(p5) if r[0].startswith("T5E")]
    check(sw and sw[0][4] == "+15.200", "zonder vinkje weer de huidige prijs (+15.200, was %s)" % (sw[0][4] if sw else None))

    # eigen verkoopgeschiedenis (prijsgrafiek in-game) gaat voor de openbare
    LOCAL_HIST.append({"item_id": "T5_MAIN_SWORD", "city": "Martlock", "quality": 1, "per_day": 150, "days": 7, "avg_price": 170000, "timescale": 1})
    p5.check("#localOn")
    p5.wait_for_function("document.getElementById('localStatus').textContent.includes('verkoopgeschiedenis')", timeout=10000)
    p5.check("#useAvg")
    p5.wait_for_timeout(600)
    sw = [r for r in rows(p5) if r[0].startswith("T5E")]
    print("eigen history:", sw[:1])
    check(sw and sw[0][6] == "14 / 150", "volume Martlock uit eigen data: 150 per dag (was %s)" % (sw[0][6] if sw else None))
    check(sw and sw[0][3].startswith("170.000") and sw[0][4] == "+63.200", "gemiddelde prijs uit eigen data: 170.000 → +63.200 (was %s)" % (sw[0][3:5] if sw else None))
    p5.click("#volBtn")
    p5.wait_for_function("!document.getElementById('scanBtn').disabled && document.getElementById('status').textContent.includes('Volume')", timeout=30000)
    sw = [r for r in rows(p5) if r[0].startswith("T5E")]
    check(sw and sw[0][6] == "14 / 150", "openbare history overschrijft eigen data niet (was %s)" % (sw[0][6] if sw else None))
    check(not err5, "geen JS-fouten na eigen history: %s" % err5)

    # ---------- sell order op de Black Market ----------
    p5.uncheck("#localOn"); p5.uncheck("#useAvg")
    SPECIAL["T5_MAIN_SWORD"]["Black Market"] = dict(sell=150000, buy=130000)
    p5.click("#modeBm")
    check(p5.is_visible("#bmSell") and not p5.is_visible("#sellMethod"), "BM-modus: keuze voor verkopen aan de BM zichtbaar")
    p5.click("#scanBtn"); p5.wait_for_function(DONE, timeout=90000)
    sw = [r for r in rows(p5) if r[0].startswith("T5E")]
    check(sw and sw[0][4] == "+24.800", "BM direct: +24.800 (was %s)" % (sw[0][4] if sw else None))
    p5.select_option("#bmSell", "order")
    sw = [r for r in rows(p5) if r[0].startswith("T5E")]
    check(sw and sw[0][3].startswith("150.000") and sw[0][4] == "+40.250", "BM sell order: 150.000 × 0,935 − 100.000 = +40.250 (was %s)" % (sw[0][3:5] if sw else None))
    p5.locator("#tbody tr").filter(has=p5.locator("span.tier", has_text=re.compile(r"^T5$"))).first.locator(".cartbtn").click()
    p5.click("#tabMandje"); p5.click("#cartGo")
    check("sell order op de Black Market plaatsen voor minstens 150.000" in p5.inner_text("#cartRoot"), "afvinklijst: sell order op de BM")
    p5.click("#tabMarkt"); p5.click("#modeCraft")
    check(p5.is_visible("#bmSell") and p5.is_visible("#sellMethod"), "craftmodus: aparte keuzes voor stad en BM")
    p5.select_option("#bmSell", "instant")
    check(not err5, "geen JS-fouten bij BM sell order: %s" % err5)

    # ---------- favorieten ----------
    SPECIAL["T5_MAIN_SWORD"]["Black Market"] = dict(sell=0, buy=130000)
    ctx6 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950})
    p6 = ctx6.new_page()
    err6 = []
    p6.on("pageerror", lambda e: err6.append(str(e)))
    p6.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), prices)
    p6.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), history)
    p6.goto("file://" + TEST_COPY)
    p6.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    p6.click("#scanBtn"); p6.wait_for_function(DONE, timeout=90000)
    T5ROW = lambda pg: pg.locator("#tbody tr").filter(has=pg.locator("span.tier", has_text=re.compile(r"^T5$"))).first
    T5ROW(p6).locator(".favbtn").click()
    check(T5ROW(p6).locator(".favbtn").inner_text() == "★" and p6.inner_text("#tabFavs") == "Favorieten (1)", "ster bewaart de route")
    p6.click("#tabFavs")
    fv = rows(p6, "#favRoot tbody tr")
    print("favorieten:", fv)
    check(fv and fv[0][2] == "Lymhurst → Black Market" and fv[0][5] == "+24.800", "favoriet toont route en winst +24.800")
    SPECIAL["T5_MAIN_SWORD"]["Black Market"] = dict(sell=0, buy=140000)
    p6.click("#favCheck")
    p6.wait_for_function("document.getElementById('favMsg') && document.getElementById('favMsg').textContent.startsWith('Gecheckt')", timeout=30000)
    fv = rows(p6, "#favRoot tbody tr")
    check(fv and fv[0][5] == "+34.400", "Check alle rekent met de nieuwe prijs: +34.400 (was %s)" % (fv[0][5] if fv else None))
    p6.click("#favRoot .cartbtn")
    check(p6.inner_text("#tabMandje") == "Mandje (1)", "favoriet in het mandje")
    # instellingen van toen blijven gelden, ook als Markt nu anders staat
    p6.click("#tabMarkt"); p6.select_option("#bmSell", "order"); p6.click("#tabFavs")
    fv = rows(p6, "#favRoot tbody tr")
    check(fv and fv[0][5] == "+34.400", "favoriet houdt zijn eigen verkoopmethode (was %s)" % (fv[0][5] if fv else None))
    p6.reload(); p6.wait_for_timeout(300)
    check(p6.inner_text("#tabFavs") == "Favorieten (1)", "favorieten bewaard na herladen")
    p6.screenshot(path=SHOT_DIR + "/favorieten.png", full_page=True)
    p6.set_viewport_size({"width": 390, "height": 844}); p6.wait_for_timeout(200)
    check(not p6.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2"), "tabs passen op mobiel")
    check(not err6, "geen JS-fouten bij favorieten: %s" % err6)

    # ---------- reisroute ----------
    p6.set_viewport_size({"width": 1400, "height": 950})
    p6.click("#tabReis")
    msg = p6.inner_text("#trMsg")
    steps = p6.inner_text("#trOut .tr-steps")
    print("reis:", msg)
    check("zones overgaan" in msg and "Martlock" in steps and "neem de uitgang naar" in steps, "standaardroute Caerleon → Martlock met stappen")
    n_steps = p6.locator("#trOut .tr-steps .chk input").count()
    check(n_steps >= 3, "meerdere stappen (%d)" % n_steps)
    p6.locator("#trOut .tr-steps .chk input").first.check()
    check("1 van " in p6.inner_text("#trOut .tr-steps"), "stap afvinken")
    p6.reload(); p6.wait_for_timeout(300)
    check("1 van " in p6.inner_text("#trOut .tr-steps"), "vinkjes bewaard na herladen")
    p6.click("#trSwap")
    check("Route Martlock → Caerleon" in p6.inner_text("#trOut"), "omwisselen")
    p6.fill("#trTo", "Bestaat Niet"); p6.press("#trTo", "Tab")
    check("bad" in (p6.get_attribute("#trTo", "class") or ""), "onbekende zone wordt rood gemarkeerd")
    p6.click("#trQuick button[data-f='to'][data-z='2000']")
    check("Route Martlock → Bridgewatch" in p6.inner_text("#trOut"), "snelknop voor bestemming")
    check(p6.locator("#trOut svg circle").count() > 5, "kaart getekend")
    check(not err6, "geen JS-fouten bij reisroute: %s" % err6)

    # ---------- vaste batches ----------
    p6.click("#tabMandje")
    p6.locator("#cartRoot input.qty").first.fill("5")
    p6.click("#cartRoot .batch-save")
    check(p6.inner_text("#tabFavs") == "Favorieten (2)", "batch bewaard: " + p6.inner_text("#tabFavs"))
    p6.reload(); p6.wait_for_timeout(300)
    p6.click("#tabFavs")
    card = p6.inner_text("#favRoot .batch")
    print("batch:", card.replace("\n", " | ")[:300])
    check("5× T5 Expert's Broadsword" in card and p6.is_disabled("#favRoot .batch-load"), "batch bewaard na herladen; eerst checken")
    p6.click("#favCheck")
    p6.wait_for_function("document.getElementById('favMsg') && document.getElementById('favMsg').textContent.startsWith('Gecheckt')", timeout=30000)
    card = p6.inner_text("#favRoot .batch")
    check("winst +172k" in card or "winst +" in card, "batch toont winst van nu: " + card.replace("\n", " | ")[:200])
    p6.click("#favRoot .batch-load")
    check("vervangen" in p6.inner_text("#favRoot .batch-load"), "waarschuwing dat het mandje wordt vervangen")
    p6.click("#favRoot .batch-load")
    check(p6.is_visible("#viewCart") and p6.evaluate("CART.items.length === 1 && CART.items[0].qty === 5"), "batch staat in het mandje met 5 stuks")
    p6.click("#cartGo")
    check("5× T5 Expert's Broadsword" in p6.inner_text("#cartRoot"), "afvinklijst van de batch")
    check(not err6, "geen JS-fouten bij batches: %s" % err6)

    # ---------- uitschieters, alarm, logboek-tijd, inzicht ----------
    def gold(route):
        now = datetime.datetime.now(datetime.timezone.utc)
        pts = [{"price": 9000 + (k % 24) * 10, "timestamp": (now - datetime.timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M:%S")} for k in range(720)]
        route.fulfill(status=200, body=json.dumps(pts), headers={"access-control-allow-origin": "*", "content-type": "application/json"})
    HIST_PRICE[("T5_MAIN_SWORD", "Martlock")] = 60000
    ctx7 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950})
    p7 = ctx7.new_page()
    err7 = []
    p7.on("pageerror", lambda e: err7.append(str(e)))
    p7.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), prices)
    p7.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), history)
    p7.route(re.compile(r"albion-online-data\.com/api/v2/stats/gold"), gold)
    p7.goto("file://" + TEST_COPY)
    p7.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    p7.click("#modeCity")
    p7.click("#scanBtn"); p7.wait_for_function(DONE, timeout=90000)
    p7.fill("#q", "expert's broadsword")
    sw = [r for r in rows(p7) if r[0].startswith("T5E")]
    print("uitschieter:", sw[:1])
    check(sw and "⚠ +100% boven gem." in sw[0][3], "uitschieter gemarkeerd: 120.000 tegen gem. 60.000")
    p7.check("#hideOut")
    check(not [r for r in rows(p7) if r[0].startswith("T5E")], "verberg uitschieters")
    p7.uncheck("#hideOut")
    HIST_PRICE.pop(("T5_MAIN_SWORD", "Martlock"), None)

    # prijsalarm
    T5ROW(p7).locator(".favbtn").click()
    p7.click("#tabFavs")
    p7.fill("#favRoot .alarm-in", "10k"); p7.press("#favRoot .alarm-in", "Tab")
    p7.check("#alOn")
    p7.wait_for_selector("#toasts .toast", timeout=5000)
    check("Prijsalarm" in p7.inner_text("#toasts"), "alarm geeft een melding als de winst boven de grens komt")
    p7.click("#toasts .toast-x")
    p7.reload(); p7.wait_for_timeout(300)
    check(p7.is_checked("#alOn") and p7.input_value("#favRoot .alarm-in") == "10.000", "alarm en grens bewaard")

    # logboek met tijd en omzet
    p7.click("#tabLogboek")
    p7.fill("#lgAmount", "60k"); p7.fill("#lgMin", "30"); p7.fill("#lgRev", "2m"); p7.click("#lgAdd")
    check("30 min" in p7.inner_text("#lgBody"), "tijd zichtbaar in het logboek")
    p7.click("#tabIns")
    p7.wait_for_function("document.getElementById('insRoot').textContent.includes('Hoe goedkoop')", timeout=10000)
    ins = p7.inner_text("#insRoot")
    print("inzicht:", ins.replace("\n", " | ")[:700])
    check("Goudprijs" in ins and "9.000" in ins, "goudprijs met grafiek")
    check("+120.000" in ins, "winst per uur: 60k in 30 min = +120.000 per uur")
    check("2m" in ins and "Nog niet" in ins, "premium-check met omzet uit logboek (2m → niet de moeite)")
    p7.screenshot(path=SHOT_DIR + "/inzicht.png", full_page=True)
    check(not err7, "geen JS-fouten bij uitschieters/alarm/inzicht: %s" % err7)

    # ---------- waar koop je je craftmaterialen ----------
    SPECIAL["T5_MAIN_SWORD"]["Black Market"] = dict(sell=0, buy=260000)
    SPECIAL["T5_METALBAR"] = {"Lymhurst": dict(sell=10000, buy=0), "Martlock": dict(sell=12000, buy=0)}
    SPECIAL["T5_LEATHER"] = {"Martlock": dict(sell=5000, buy=0), "Lymhurst": dict(sell=7000, buy=0)}
    ctx8 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 950})
    p8 = ctx8.new_page()
    err8 = []
    p8.on("pageerror", lambda e: err8.append(str(e)))
    p8.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), prices)
    p8.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), history)
    p8.goto("file://" + TEST_COPY)
    p8.click("#modeCraft")
    check(p8.is_visible("#matMode"), "craftmodus: keuze waar je materialen koopt")
    for tt in ["T4", "T6"]:
        p8.locator("#tiers button", has_text=tt).first.click()
    p8.locator("#ench button", has_text=".1").first.click(); p8.locator("#ench button", has_text=".2").first.click()
    p8.locator("#ench button", has_text=".3").first.click(); p8.locator("#ench button", has_text=".4").first.click()
    p8.fill("#minProfit", "0"); p8.fill("#q", "expert's broadsword")
    p8.click("#scanBtn"); p8.wait_for_function(DONE, timeout=90000)
    k = 1 / 1.33
    def cost_cell():
        rr = [r for r in rows(p8) if r[0].startswith("T5E")]
        return rr[0][2].split(" ")[0] if rr else None
    fmtc = lambda v: "{:,}".format(round(v)).replace(",", ".")
    check(cost_cell() == fmtc(200000 * k), "goedkoopste per materiaal: %s (was %s)" % (fmtc(200000 * k), cost_cell()))
    p8.select_option("#matMode", "onecity")
    check(cost_cell() == fmtc(216000 * k), "alles in één stad (Lymhurst): %s (was %s)" % (fmtc(216000 * k), cost_cell()))
    p8.select_option("#matMode", "Martlock")
    check(not p8.is_visible("#buyCities"), "bij een vaste stad zijn de stadknoppen verborgen")
    p8.click("#scanBtn"); p8.wait_for_function(DONE, timeout=90000)
    check(cost_cell() == fmtc(232000 * k), "alles in Martlock: %s (was %s)" % (fmtc(232000 * k), cost_cell()))
    p8.select_option("#matMode", "craftcity")
    p8.click("#scanBtn"); p8.wait_for_function(DONE, timeout=90000)
    check(cost_cell() == fmtc(216000 * k), "alles in de craftstad (Lymhurst voor zwaarden): %s (was %s)" % (fmtc(216000 * k), cost_cell()))
    p8.locator("#tbody tr").first.locator(".recipe-btn").click()
    det = p8.inner_text("tr.detail")
    check("Lymhurst" in det and "Martlock" not in det, "recept: alle materialen uit Lymhurst")
    check(not err8, "geen JS-fouten bij materiaalkeuze: %s" % err8)

    # ---------- aparte leeftijd voor de BM ----------
    p8.click("#tabMarkt"); p8.click("#modeBm")
    res = p8.evaluate("""() => {
      const id = 'T4_MAIN_SWORD', key = id + '|1', now = Date.now(), h = 3600000;
      const keep = store.get(key);
      store.set(key, {Lymhurst:{sell:10000, sellT:now - 10*h, buy:9000, buyT:now - 10*h},
                      'Black Market':{sell:0, sellT:null, buy:30000, buyT:now - 5*60000}});
      const run = o => withSettings({mode:'bm', buyCities:new Set(['Lymhurst']), minProfit:-Infinity, minRoi:-Infinity, maxRoi:0, showAll:true,
                                     q:'', tree:'', cats:new Set(['w']), tiers:new Set([4]), ench:new Set([0]), art:'all', useAvg:false, ...o},
                                    () => computeFlips().rows.filter(r => r.id === id).length);
      const out = {same6:run({maxAge:6, maxAgeBm:0}), city12bm15:run({maxAge:12, maxAgeBm:0.25}),
                   bmTooOld:(store.get(key)['Black Market'].buyT = now - 40*60000, run({maxAge:12, maxAgeBm:0.25}))};
      if(keep) store.set(key, keep); else store.delete(key);
      return out;
    }""")
    print("leeftijd:", res)
    check(res["same6"] == 0, "10 uur oude stadsprijs valt weg bij 6 uur")
    check(res["city12bm15"] == 1, "steden 12 u + BM 15 min: rij zichtbaar")
    check(res["bmTooOld"] == 0, "BM-prijs van 40 min valt weg bij BM 15 min")
    p8.select_option("#maxAgeBm", "0.25"); p8.reload(); p8.wait_for_timeout(400)
    check(p8.input_value("#maxAgeBm") == "0.25", "BM-leeftijd blijft bewaard")
    p8.select_option("#maxAgeBm", "0")

    # ---------- spec-tree ----------
    p8.click("#tabMarkt"); p8.click("#modeCraft")
    check(p8.locator("#tree option[value=froststaff]").count() == 1, "Frost Staff staat in de spec-tree-lijst")
    p8.select_option("#tree", "froststaff")
    check(p8.input_value("#q") == "", "zoekveld leeg bij kiezen van een tree")
    check("Glacial Staff" in p8.inner_text("#treeInfo"), "uitleg noemt de items in de tree: %s" % p8.inner_text("#treeInfo")[:120])
    n_tree = p8.evaluate("selectedProducts().length")
    bad = p8.evaluate("selectedProducts().filter(p => p.it.st !== 'froststaff').length")
    check(n_tree > 0 and bad == 0, "selectie bevat alleen frost staffs (%d producten)" % n_tree)
    p8.click("#scanBtn"); p8.wait_for_function(DONE, timeout=120000)
    ids = p8.evaluate("withSettings({minProfit:-Infinity, minRoi:-Infinity, maxRoi:0, showAll:true}, () => compute().rows.map(r => r.id))")
    check(len(ids) > 0 and all("FROST" in i or "GLACIAL" in i or "ICE" in i or "STAFF" in i for i in ids), "scan geeft alleen frost-staff-rijen (%d)" % len(ids))
    # ---------- craften: waarom niks? ----------
    d = p8.evaluate("""() => withSettings({bmSell:'order', craftSell:new Set(['Black Market']), minProfit:1e9, minRoi:0, tree:'', q:''}, () => {
        const r = computeCrafts().diag; return {items:r.items, noOrder:r.noOrder, below:r.below, best:!!r.best}; })""")
    d2 = p8.evaluate("""() => withSettings({bmSell:'instant', craftSell:new Set(['Black Market']), minProfit:1e9, minRoi:0, tree:'', q:''}, () => {
        const r = computeCrafts().diag; return {items:r.items, noOrder:r.noOrder, below:r.below, best:!!r.best}; })""")
    print("diag order:", d, "instant:", d2)
    check(d["items"] > 0 and d["noOrder"] > 0, "BM sell order zonder BM-sell orders wordt als reden geteld")
    check(d2["below"] > 0 and d2["best"], "direct aan BM: rijen berekend, beste rij bekend")
    p8.evaluate("S.minProfit = 1e9; S.bmSell = 'instant'; render()")
    txt = p8.inner_text("#empty")
    check("Waarom" in txt and "Beste die er was" in txt, "lege craftlijst legt uit waarom: %s" % txt[:160])
    p8.evaluate("S.minProfit = 5000; render()")

    p8.reload(); p8.wait_for_timeout(500)
    check(p8.input_value("#tree") == "froststaff", "spec-tree blijft bewaard na herladen")
    check(p8.evaluate("evalFav({mode:'city', itemId:'T5_MAIN_SWORD', quality:1, from:'Lymhurst', to:'Martlock'}) !== undefined"), "favorieten werken los van de tree")
    p8.select_option("#tree", "")
    check(p8.evaluate("selectedProducts().some(p => p.it.st !== 'froststaff')"), "terug naar alle items")
    check(not err8, "geen JS-fouten bij spec-tree: %s" % err8)

    # ---------- handmatig toevoegen ----------
    p8.click("#tabMandje")
    p8.click("#manualBox summary")
    p8.fill("#mItem", "T5 Expert's Broadsword"); p8.press("#mItem", "Tab")
    check(p8.locator("#mEnch option").count() == 5, "enchants .0–.4 beschikbaar")
    p8.select_option("#mKind", "flip"); p8.select_option("#mFrom", "Lymhurst"); p8.select_option("#mTo", "Black Market"); p8.fill("#mQty", "3")
    p8.click("#mAdd")
    p8.wait_for_function("document.getElementById('mMsg').className.includes('ok') || document.getElementById('mMsg').className.includes('err')", timeout=30000)
    mm = p8.inner_text("#mMsg")
    print("handmatig:", mm)
    check("3× T5 Expert's Broadsword toegevoegd" in mm and "+139.200" in mm, "flip handmatig: 260.000 × 0,92 − 100.000 = +139.200")
    check(p8.evaluate("CART.items.some(x => x.key.startsWith('manual|') && x.qty === 3)"), "staat in het mandje met 3 stuks")
    p8.select_option("#mKind", "craft"); p8.fill("#mQty", "1")
    check(not p8.is_visible("#mFrom"), "bij craften geen ‘kopen in’")
    p8.click("#mAdd")
    p8.wait_for_function("document.getElementById('mMsg').textContent.includes('toegevoegd') && !document.getElementById('mMsg').textContent.startsWith('3×')", timeout=30000)
    check(p8.evaluate("CART.items.some(x => x.craft && x.key.startsWith('manual|'))"), "craft handmatig toegevoegd met recept")
    p8.click("#mFav")
    p8.wait_for_function("document.getElementById('mMsg').textContent.includes('bewaard in Favorieten')", timeout=30000)
    check(p8.inner_text("#tabFavs").startswith("Favorieten (1)"), "handmatig als favoriet bewaard")
    p8.fill("#mItem", "bestaat niet"); p8.click("#mAdd")
    check("niet gevonden" in p8.inner_text("#mMsg"), "onbekend item geeft uitleg")
    p8.fill("#mItem", "broadsword"); p8.click("#mAdd")
    check("items passen hierbij" in p8.inner_text("#mMsg"), "te vaag geeft suggesties: %s" % p8.inner_text("#mMsg"))
    # los typen + eigen prijzen
    p8.select_option("#mKind", "flip"); p8.select_option("#mFrom", "Lymhurst"); p8.select_option("#mTo", "Black Market")
    p8.fill("#mItem", "t5.0 broadsword"); p8.press("#mItem", "Tab")
    check(p8.input_value("#mEnch") == "0", "enchant uit ‘t5.0’ gehaald")
    p8.fill("#mBuy", "100k"); p8.fill("#mSell", "300k"); p8.fill("#mQty", "2")
    n_before = p8.evaluate("CART.items.length")
    p8.click("#mAdd")
    p8.wait_for_function("document.getElementById('mMsg').textContent.startsWith('2×')", timeout=30000)
    mm = p8.inner_text("#mMsg"); print("eigen prijs:", mm)
    check("+176.000" in mm, "eigen prijzen: 300.000 × 0,92 − 100.000 = +176.000")
    check(p8.evaluate("CART.items.length") == n_before, "zelfde route telt op bij bestaand item")
    p8.fill("#mBuy", "abc"); p8.click("#mAdd")
    check("snap ik niet" in p8.inner_text("#mMsg"), "onzinprijs geeft uitleg")
    p8.fill("#mBuy", ""); p8.fill("#mSell", "")
    # oude prijs mag, met waarschuwing
    p8.evaluate("S.maxAge = 0.0001")
    p8.click("#mAdd")
    p8.wait_for_function("document.getElementById('mMsg').textContent.includes('toegevoegd')", timeout=30000)
    check("uur oud" in p8.inner_text("#mMsg") or "0 uur" in p8.inner_text("#mMsg"), "oude prijs: toegevoegd met waarschuwing: %s" % p8.inner_text("#mMsg"))
    check(not err8, "geen JS-fouten bij handmatig toevoegen: %s" % err8)

    browser.close()

print("\nFAILURES:", len(failures))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
