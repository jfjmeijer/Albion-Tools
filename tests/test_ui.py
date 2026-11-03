import datetime
import json
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

SRC, TEST_COPY, SHOT_DIR = sys.argv[1], sys.argv[2], sys.argv[3]

# testkopie met korte pauze tussen verzoeken (de echte pauze is 1100 ms)
html = open(SRC, encoding="utf-8").read()
assert "const MIN_GAP_MS = 1100;" in html
open(TEST_COPY, "w", encoding="utf-8").write(html.replace("const MIN_GAP_MS = 1100;", "const MIN_GAP_MS = 5;"))

ZERO = "0001-01-01T00:00:00"


def iso(minutes_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


SPECIAL = {
    "T5_MAIN_SWORD": {
        "Lymhurst": dict(sell=100000, buy=90000),
        "Martlock": dict(sell=140000, buy=120000),
        "Black Market": dict(sell=0, buy=130000),
    },
    "T4_BAG@2": {
        "Lymhurst": dict(sell=50000, buy=0),
        "Black Market": dict(sell=0, buy=80000, age=600),
    },
    "T6_2H_BOW": {
        "Martlock": dict(sell=1000, buy=0),
        "Black Market": dict(sell=0, buy=100000),
    },
}
DEFAULT_CITY = dict(sell=150000, buy=100000)
DEFAULT_BM = dict(sell=0, buy=100000)

requests_seen = []


def record(item, loc, quality):
    spec = SPECIAL.get(item, {}).get(loc)
    if spec is None:
        spec = DEFAULT_BM if loc == "Black Market" else DEFAULT_CITY
    age = spec.get("age", 5)
    sell, buy = spec["sell"], spec["buy"]
    return {
        "item_id": item, "city": loc, "quality": quality,
        "sell_price_min": sell, "sell_price_min_date": iso(age) if sell else ZERO,
        "sell_price_max": sell, "sell_price_max_date": iso(age) if sell else ZERO,
        "buy_price_min": 0, "buy_price_min_date": ZERO,
        "buy_price_max": buy, "buy_price_max_date": iso(age) if buy else ZERO,
    }


def handler(route):
    url = route.request.url
    requests_seen.append(url)
    parsed = urllib.parse.urlparse(url)
    ids = urllib.parse.unquote(parsed.path.split("/prices/")[1]).replace(".json", "").split(",")
    qs = urllib.parse.parse_qs(parsed.query)
    locs = qs["locations"][0].split(",")
    quals = [int(x) for x in qs["qualities"][0].split(",")]
    body = [record(i, l, q) for i in ids for l in locs for q in quals]
    route.fulfill(status=200, body=json.dumps(body),
                  headers={"access-control-allow-origin": "*", "content-type": "application/json"})


hist_seen = []
HIST_SPECIAL = {
    # (item, locatie) -> lijst met aantallen voor de afgelopen volle dagen (dag 1 = gisteren); None = geen record
    ("T5_MAIN_SWORD", "Lymhurst"): [14] * 7,
    ("T5_MAIN_SWORD", "Martlock"): [4, 0, 4, 0, 4, 0, 0],
    ("T5_MAIN_SWORD", "Black Market"): None,
}
HIST_DEFAULT = 20


def hist_record(item, loc, quality):
    spec = HIST_SPECIAL.get((item, loc), HIST_DEFAULT)
    if spec is None:
        return None
    counts = spec if isinstance(spec, list) else [spec] * 7
    now = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    data = [{"item_count": 999, "avg_price": 1, "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S")}]   # vandaag: onvolledig
    for k, n in enumerate(counts, start=1):
        if n:
            data.append({"item_count": n, "avg_price": 100000 + k, "timestamp": (now - datetime.timedelta(days=k)).strftime("%Y-%m-%dT%H:%M:%S")})
    data.append({"item_count": 777, "avg_price": 1, "timestamp": (now - datetime.timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%S")})  # te oud
    return {"location": loc, "item_id": item, "quality": quality, "data": data}


def hist_handler(route):
    url = route.request.url
    hist_seen.append(url)
    parsed = urllib.parse.urlparse(url)
    ids = urllib.parse.unquote(parsed.path.split("/history/")[1]).replace(".json", "").split(",")
    qs = urllib.parse.parse_qs(parsed.query)
    locs = qs["locations"][0].split(",")
    quals = [int(x) for x in qs["qualities"][0].split(",")]
    body = [r for i in ids for l in locs for q in quals for r in [hist_record(i, l, q)] if r]
    route.fulfill(status=200, body=json.dumps(body),
                  headers={"access-control-allow-origin": "*", "content-type": "application/json"})


failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def rows(page):
    data = page.eval_on_selector_all(
        "#tbody tr", "rs => rs.map(r => Array.from(r.children).map(c => c.innerText.replace(/\\s+/g,' ').trim()))")
    # de pijl krijgt in de pagina marge i.p.v. spaties
    for r in data:
        if len(r) > 1:
            r[1] = re.sub(r"\s*→\s*", " → ", r[1])
        if len(r) > 6:
            r[6] = re.sub(r"\s*/\s*", " / ", r[6])
    return data


def find(page, text):
    return [r for r in rows(page) if text in r[0]]


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), handler)
    page.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), hist_handler)
    page.goto("file://" + TEST_COPY)
    page.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")

    est = page.inner_text("#est")
    print("estimate:", est)
    check("verzoeken" in est, "schatting van verzoeken zichtbaar")

    # verbindingstest
    page.click("#testBtn")
    page.wait_for_function("document.getElementById('status').textContent.includes('Verbinding OK')", timeout=10000)
    check(True, "test verbinding meldt OK")

    # scan in Black Market-modus
    requests_seen.clear()
    page.click("#scanBtn")
    page.wait_for_function("document.getElementById('status').textContent.startsWith('Klaar') && !document.getElementById('scanBtn').disabled", timeout=90000)
    n_req = len(requests_seen)
    print("verzoeken:", n_req)
    check(all(len(u) < 4096 for u in requests_seen), "alle URL's korter dan 4096 tekens (max %d)" % max(len(u) for u in requests_seen))
    check(any("Black%20Market" in u and "Fort%20Sterling" in u for u in requests_seen), "locaties correct URL-gecodeerd")
    check(all("qualities=1" in u for u in requests_seen), "kwaliteit 1 meegestuurd")
    check(str(n_req) in est, "geschat aantal verzoeken (%s) klopt met werkelijk (%d)" % (est, n_req))

    r = rows(page)
    print("rijen BM:", r)
    sw = find(page, "Broadsword")
    check(len(r) == 1 and len(sw) == 1, "precies 1 kans met standaardfilters")
    if sw:
        c = sw[0]
        check(c[0].startswith("T5") and "Expert's Broadsword" in c[0], "naam/tier: " + c[0])
        check(c[1] == "Lymhurst → Black Market", "route: " + c[1])
        check("100.000" in c[2] and "130.000" in c[3], "prijzen: %s | %s" % (c[2], c[3]))
        check(c[4] == "+24.800", "winst premium = +24.800 (was %s)" % c[4])
        check(c[5] == "24.8%", "ROI = 24.8%% (was %s)" % c[5])

    page.screenshot(path=SHOT_DIR + "/bm.png")

    # --- volume (history) werd automatisch opgehaald ---
    check(page.is_checked("#volAuto"), "volume automatisch ophalen staat standaard aan")
    check(len(hist_seen) == 1, "1 history-verzoek voor 1 rij (was %d)" % len(hist_seen))
    check(all(len(u) < 4096 for u in hist_seen), "history-URL's korter dan 4096 tekens")
    check(all("time-scale=24" in u and "/stats/history/" in u for u in hist_seen), "history met dagschaal opgevraagd")
    check(any("T5_MAIN_SWORD" in u and "Lymhurst" in u and "Black%20Market" in u for u in hist_seen), "history vraagt koopstad en Black Market op")
    check(sw and sw[0][6] == "14 / –", "volume BM-route: 14 per dag in Lymhurst (vandaag en oude dag tellen niet), BM geen data (was %s)" % (sw[0][6] if sw else None))
    check(sw and sw[0][7] == "–", "winst/dag onbekend zolang de BM-kant ontbreekt (was %s)" % (sw[0][7] if sw else None))
    st_vol = page.inner_text("#status")
    check("Lymhurst 1/1" in st_vol and "Black Market 0/1" in st_vol and "geen history-data" in st_vol, "status meldt dekking en BM-waarschuwing: " + st_vol[:160])

    # filters: ROI-cap uit -> bow met absurde ROI verschijnt
    page.fill("#maxRoi", "0")
    bow = find(page, "Bow")
    check(len(bow) == 1 and bow[0][4] == "+95.000", "uitschieter zichtbaar zonder ROI-cap: %s" % (bow[0][4] if bow else None))
    page.fill("#maxRoi", "200")
    check(len(find(page, "Bow")) == 0, "uitschieter weer verborgen met ROI-cap")

    # verouderde data
    check(len(find(page, "Bag")) == 0, "verouderde BM-data (10 u) verborgen bij max 6 u")
    page.select_option("#maxAge", "24")
    bag = find(page, "Bag")
    check(len(bag) == 1 and bag[0][0].startswith("T4.2") and bag[0][4] == "+26.800", "bag verschijnt bij 24 u: %s" % (bag[0] if bag else None))
    page.select_option("#maxAge", "6")

    # zoekfilter
    page.fill("#q", "broadsword")
    check(len(rows(page)) == 1, "zoekfilter op naam")

    # geen premium
    page.uncheck("#premium")
    sw = find(page, "Broadsword")
    check(sw and sw[0][4] == "+19.600", "zonder premium winst = +19.600 (was %s)" % (sw[0][4] if sw else None))
    page.check("#premium")

    # buy order plaatsen
    page.select_option("#buyMethod", "order")
    sw = find(page, "Broadsword")
    check(sw and sw[0][4] == "+32.550" and sw[0][1] == "Lymhurst → Black Market", "buy order: +32.550 (was %s)" % (sw[0][4:5] if sw else None))
    page.select_option("#buyMethod", "instant")

    # stad -> stad
    page.click("#modeCity")
    sw = find(page, "Broadsword")
    check(sw and sw[0][4] == "+15.200" and sw[0][1] == "Lymhurst → Martlock", "stad->stad instant: +15.200 (was %s)" % (sw[0][1:5] if sw else None))
    page.select_option("#sellMethod", "order")
    sw = find(page, "Broadsword")
    check(sw and sw[0][4] == "+40.250", "stad->stad sell order: +40.250 (was %s)" % (sw[0][1:5] if sw else None))
    page.screenshot(path=SHOT_DIR + "/city.png")

    # alle routes per item
    page.check("#showAll")
    n_all = len(find(page, "Broadsword"))
    check(n_all > 1, "alle routes tonen geeft meer dan 1 rij (%d)" % n_all)
    page.uncheck("#showAll")

    # sorteren op naam (klik op header) en kopieerknop
    page.click("#thead th:nth-child(1)")
    check(page.get_attribute("#thead th:nth-child(1)", "aria-sort") == "ascending", "sorteren op naam")
    page.click("#tbody tr:first-child .copy")
    page.wait_for_timeout(100)
    check(page.inner_text("#tbody tr:first-child .copy") in ("Gekopieerd", "Mislukt"), "kopieerknop reageert")

    # instellingen blijven bewaard na herladen
    page.select_option("#server", "west")
    page.reload()
    check(page.input_value("#server") == "west", "server-instelling bewaard na herladen")

    check(not errors, "geen JS-fouten in console: %s" % errors)

    # mobiele weergave
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=SHOT_DIR + "/mobile.png", full_page=False)
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2")
    check(not overflow, "geen horizontale overflow op mobiel")

    # API onbereikbaar -> duidelijke foutmelding
    page2 = ctx.new_page()
    page2.route(re.compile(r"albion-online-data\.com/"), lambda r: r.abort())
    page2.goto("file://" + TEST_COPY)
    page2.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    page2.click("#scanBtn")
    page2.wait_for_function("document.getElementById('status').className.includes('err')", timeout=15000)
    msg = page2.inner_text("#status")
    check("geen antwoord" in msg, "foutmelding bij onbereikbare API: " + msg[:70])
    check(page2.is_enabled("#scanBtn") and not page2.is_enabled("#stopBtn"), "knoppen hersteld na fout")

    # handmatig plakken
    page3 = ctx.new_page()
    page3.goto("file://" + TEST_COPY)
    page3.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    sample = [record("T5_MAIN_SWORD", "Lymhurst", 1), record("T5_MAIN_SWORD", "Black Market", 1)]
    sample[0]["sell_price_min"] = 100000
    sample[1]["buy_price_max"] = 130000
    page3.click("#modeBm")
    page3.click("details summary")
    page3.fill("#paste", json.dumps(sample))
    page3.click("#pasteBtn")
    sw = find(page3, "Broadsword")
    check(sw and sw[0][4] == "+24.800", "handmatig plakken geeft dezelfde winst")
    page3.fill("#paste", json.dumps([hist_record("T5_MAIN_SWORD", "Lymhurst", 1)]))
    page3.click("#pasteBtn")
    check("history" in page3.inner_text("#status"), "geplakte history wordt herkend")
    sw = find(page3, "Broadsword")
    check(sw and sw[0][6] == "14 / ?", "geplakte history vult volume (was %s)" % (sw[0][6] if sw else None))

    # ================= nieuw: grondstoffen en craften =================
    DUMP = json.load(open("/home/claude/dl/itemsjson/items.json", encoding="utf-8"))["items"]

    def dump_node(uid):
        for k, v in DUMP.items():
            if k.startswith("@"):
                continue
            for n in (v if isinstance(v, list) else [v]):
                if isinstance(n, dict) and n.get("@uniquename") == uid:
                    return n
        return None

    def recipe_of(uid):
        cr = dump_node(uid)["craftingrequirements"]
        cr = cr[0] if isinstance(cr, list) else cr
        cl = cr["craftresource"]
        cl = cl if isinstance(cl, list) else [cl]
        return {c["@uniquename"]: int(c["@count"]) for c in cl}

    sword = recipe_of("T5_MAIN_SWORD")
    refine = recipe_of("T5_METALBAR")
    check(sword == {"T5_METALBAR": 16, "T5_LEATHER": 8}, "recept T5 broadsword in game-data: %s" % sword)
    check(refine == {"T5_ORE": 3, "T4_METALBAR": 1}, "recept T5 steel bar in game-data: %s" % refine)

    SPECIAL["T5_MAIN_SWORD"]["Black Market"] = dict(sell=0, buy=260000)
    SPECIAL["T5_MAIN_SWORD@2"] = {"Black Market": dict(sell=0, buy=600000)}
    SPECIAL["T5_ORE"] = {"Lymhurst": dict(sell=2000, buy=0), "Martlock": dict(sell=0, buy=3500)}
    SPECIAL["T4_METALBAR"] = {"Lymhurst": dict(sell=7000, buy=0)}
    SPECIAL["T5_METALBAR"] = {"Lymhurst": dict(sell=10000, buy=0), "Martlock": dict(sell=0, buy=15000)}
    SPECIAL["T5_LEATHER"] = {"Martlock": dict(sell=5000, buy=0)}
    SPECIAL["T5_METALBAR_LEVEL2@2"] = {"Lymhurst": dict(sell=25000, buy=0)}
    SPECIAL["T5_LEATHER_LEVEL2@2"] = {"Martlock": dict(sell=12000, buy=0)}

    ctx2 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 900})
    p4 = ctx2.new_page()
    errors4 = []
    p4.on("pageerror", lambda e: errors4.append(str(e)))
    p4.on("console", lambda m: errors4.append(m.text) if m.type == "error" else None)
    p4.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), handler)
    p4.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), hist_handler)
    p4.goto("file://" + TEST_COPY)
    p4.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")

    def chip(group, text):
        p4.locator("#%s button" % group, has_text=text).first.click()

    def run_scan():
        p4.evaluate("document.getElementById('status').textContent = ''")
        requests_seen.clear()
        p4.click("#scanBtn")
        p4.wait_for_function("document.getElementById('status').textContent.startsWith('Klaar') && !document.getElementById('scanBtn').disabled", timeout=90000)

    # BM-modus: grondstoffen horen daar niet
    check(p4.locator("#cats button", has_text="Ruwe").first.is_disabled(), "BM-modus: 'Ruwe grondstoffen' is uitgeschakeld")
    check(p4.locator("#cats button", has_text="Gerefinde").first.is_disabled(), "BM-modus: 'Gerefinde materialen' is uitgeschakeld")

    # --- stad -> stad met grondstoffen ---
    p4.click("#modeCity")
    check(not p4.locator("#cats button", has_text="Ruwe").first.is_disabled(), "stad->stad: grondstoffen aan te zetten")
    for t in ["Wapens", "Armor", "Off-hand", "Mantels", "Tassen"]:
        chip("cats", t)
    chip("cats", "Ruwe")
    chip("cats", "Gerefinde")
    chip("tiers", "T4"); chip("tiers", "T6")
    for e in [".1", ".2", ".3", ".4"]:
        chip("ench", e)
    p4.fill("#minProfit", "100")
    run_scan()
    ids_requested = ",".join(urllib.parse.unquote(u) for u in requests_seen)
    check("T5_ORE" in ids_requested and "T5_MAIN_SWORD" not in ids_requested, "alleen grondstof-ID's opgevraagd (%d verzoek(en))" % len(requests_seen))
    check(all(len(u) < 4096 for u in requests_seen), "URL-lengte ok bij grondstoffen")
    ore = find(p4, "Ore")
    print("ore rijen:", ore)
    check(len(ore) == 1 and ore[0][4] == "+1.360" and ore[0][1] == "Lymhurst → Martlock" and ore[0][0].startswith("T5"),
          "grondstof-flip T5 ore: +1.360 (was %s)" % (ore[0][1:5] if ore else None))
    bar = find(p4, "Steel Bar")
    check(len(bar) == 1 and bar[0][4] == "+4.400", "gerefinde flip T5 steel bar: +4.400 (was %s)" % (bar[0][1:5] if bar else None))
    p4.screenshot(path=SHOT_DIR + "/resources.png")

    # --- craften: gear ---
    p4.click("#modeCraft")
    check(p4.locator("#cats button", has_text="Ruwe").first.is_disabled(), "craft-modus: ruwe grondstoffen uitgeschakeld")
    check(p4.is_disabled("#quality"), "craft-modus: kwaliteitskeuze uitgeschakeld")
    check(p4.is_visible("#craftBox"), "craft-instellingen zichtbaar")
    chip("cats", "Gerefinde")   # uit
    chip("cats", "Wapens")      # aan
    chip("ench", ".2")          # .0 en .2
    p4.fill("#minProfit", "5000")
    run_scan()
    decoded = [urllib.parse.unquote(u) for u in requests_seen]
    check(any("T5_MAIN_SWORD@2" in u and "Black Market" in u for u in decoded), "producten met BM-locatie opgevraagd")
    check(any("T5_METALBAR_LEVEL2@2" in u and "Black Market" not in u for u in decoded), "enchanted materialen (T5_METALBAR_LEVEL2@2) zonder BM opgevraagd")
    check(all("qualities=1" in u and "qualities=1," not in u for u in decoded), "craft: altijd alleen kwaliteit 1")

    rrr = 1 - 1 / 1.33        # zwaard: Lymhurst is de bonusstad (0,18 basis + 0,15)
    rrr_ref = 1 - 1 / 1.58    # steel bar: Thetford refine-bonus (0,18 + 0,40)
    cost0 = (16 * 10000 + 8 * 5000) * (1 - rrr)
    exp0 = round(260000 * 0.96 - cost0)
    cost2 = (16 * 25000 + 8 * 12000) * (1 - rrr)
    exp2 = round(600000 * 0.96 - cost2)
    print("verwacht .0:", exp0, "ROI", exp0 / cost0 * 100, "| verwacht .2:", exp2, "ROI", exp2 / cost2 * 100)
    sw = find(p4, "Broadsword")
    print("broadsword craft rijen:", sw)
    fmt_nl = lambda n: "+{:,}".format(n).replace(",", ".")
    r0 = [r for r in sw if r[0].startswith("T5Expert") or r[0].startswith("T5 ")]
    r0 = [r for r in sw if re.match(r"^T5(?!\.)", r[0])]
    r2 = [r for r in sw if r[0].startswith("T5.2")]
    check(len(r0) == 1 and r0[0][4] == fmt_nl(exp0) and r0[0][1] == "craft Lymhurst → Black Market", "craft T5 broadsword: %s (was %s)" % (fmt_nl(exp0), r0[0][1:5] if r0 else None))
    check(len(r0) == 1 and r0[0][5] == "%.1f%%" % (exp0 / cost0 * 100), "craft ROI %.1f%% (was %s)" % (exp0 / cost0 * 100, r0[0][5] if r0 else None))
    check(len(r2) == 1 and r2[0][4] == fmt_nl(exp2), "craft T5.2 broadsword (enchanted recept): %s (was %s)" % (fmt_nl(exp2), r2[0][4] if r2 else None))
    check(len(r0) == 1 and "{:,}".format(round(cost0)).replace(",", ".") in r0[0][2], "kosten zichtbaar: %s" % (r0[0][2] if r0 else None))

    # receptdetails
    p4.locator("#tbody tr", has_text="Broadsword").first.locator(".recipe-btn").click()
    det = p4.inner_text("tr.detail")
    print("detail:", det.replace("\n", " | ")[:300])
    check("16×" in det and "8×" in det and "Lymhurst" in det and "Martlock" in det and "RRR 24,8%" in det and "craften in Lymhurst" in det, "receptdetails tonen materialen, steden en RRR")

    # eigen RRR
    p4.select_option("#rrr", "custom")
    check(p4.is_visible("#rrrCustomWrap"), "eigen RRR-veld zichtbaar")
    p4.fill("#rrrCustom", "50")
    r0 = [r for r in find(p4, "Broadsword") if re.match(r"^T5(?!\.)", r[0])]
    exp_c = round(260000 * 0.96 - 200000 * 0.5)
    check(len(r0) == 1 and r0[0][4] == fmt_nl(exp_c), "eigen RRR 50%%: %s (was %s)" % (fmt_nl(exp_c), r0[0][4] if r0 else None))
    p4.select_option("#rrr", "best")

    # stationkosten
    p4.fill("#stationFee", "1000")
    fee = (16 * 32 + 8 * 32) * 0.1125 * 1000 / 100
    exp_f = round(260000 * 0.96 - (cost0 + fee))
    r0 = [r for r in find(p4, "Broadsword") if re.match(r"^T5(?!\.)", r[0])]
    check(len(r0) == 1 and r0[0][4] == fmt_nl(exp_f), "stationkosten (fee %.0f): %s (was %s)" % (fee, fmt_nl(exp_f), r0[0][4] if r0 else None))
    p4.fill("#stationFee", "0")

    # geen premium
    p4.uncheck("#premium")
    exp_np = round(260000 * 0.92 - cost0)
    r0 = [r for r in find(p4, "Broadsword") if re.match(r"^T5(?!\.)", r[0])]
    check(len(r0) == 1 and r0[0][4] == fmt_nl(exp_np), "craft zonder premium: %s (was %s)" % (fmt_nl(exp_np), r0[0][4] if r0 else None))
    p4.check("#premium")
    p4.screenshot(path=SHOT_DIR + "/craft.png")

    # artefact-filter
    n_af = p4.evaluate("BASES.filter(b => b.af).length")
    check(p4.evaluate("!BASEMAP['MAIN_SWORD'].af && !!BASEMAP['2H_DUALAXE_KEEPER'].af && !BASEMAP['ARMOR_PLATE_ROYAL'].af"),
          "artefact-vlag: zwaard nee, KEEPER-dualaxe ja, royal armor nee (%d artefact-bases)" % n_af)
    rec_af = p4.evaluate("recipeFor(BASEMAP['2H_DUALAXE_KEEPER'], 5, 0)")
    check(any(c[0] == "T5_ARTEFACT_2H_DUALAXE_KEEPER" and c[2] == 0 for c in rec_af), "artefact zit in recept en wordt niet teruggegeven: %s" % rec_af)
    p4.select_option("#art", "only")
    check(len(find(p4, "Broadsword")) == 0, "filter 'alleen artefact-items' verbergt het (niet-artefact) zwaard")
    ids_only = p4.evaluate("selectedProducts().every(p => p.it.af)")
    check(ids_only, "scan-selectie bevat bij 'alleen artefact' enkel artefact-items")
    p4.select_option("#art", "none")
    check(len(find(p4, "Broadsword")) == 2, "filter 'geen artefact-items' toont het zwaard weer")
    p4.select_option("#art", "all")

    # --- craften: refinen ---
    chip("cats", "Wapens")      # uit
    chip("cats", "Gerefinde")   # aan
    chip("ench", ".2")          # alleen .0
    for c in ["Black Market", "Caerleon", "Bridgewatch", "Fort Sterling", "Lymhurst", "Thetford", "Brecilien"]:
        chip("sellCities", c)
    run_scan()
    bar = find(p4, "Steel Bar")
    cost_r = (3 * 2000 + 1 * 7000) * (1 - rrr_ref)
    exp_r = round(15000 * 0.96 - cost_r)
    print("refine rijen:", bar, "verwacht", exp_r, "ROI", exp_r / cost_r * 100)
    bar5 = [r for r in bar if r[0].startswith("T5")]
    check(len(bar5) == 1 and bar5[0][4] == fmt_nl(exp_r) and bar5[0][1] == "craft Thetford → Martlock", "refinen T5 steel bar: %s (was %s)" % (fmt_nl(exp_r), bar5[0][1:5] if bar5 else None))
    check(len(bar5) == 1 and bar5[0][5] == "%.1f%%" % (exp_r / cost_r * 100), "refine ROI %.1f%%" % (exp_r / cost_r * 100))
    check(len(bar5) == 1 and bar5[0][6] == "20", "craft/refine: één volumegetal voor de verkoopplek (was %s)" % (bar5[0][6] if bar5 else None))
    pot_exp = "{:,}".format(round((15000 * 0.96 - cost_r) * 20)).replace(",", ".")
    check(len(bar5) == 1 and bar5[0][7] == pot_exp, "craft/refine: winst/dag = winst x 20 = %s (was %s)" % (pot_exp, bar5[0][7] if bar5 else None))

    check(not errors4, "geen JS-fouten in nieuwe modi: %s" % errors4)

    # instellingen (modus) blijven na herladen
    p4.reload()
    check(p4.get_attribute("#modeCraft", "aria-pressed") == "true", "craft-modus bewaard na herladen")

    # ================= volume: handmatig, filter, sorteren, instelling =================
    ctx3 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 900})
    p5 = ctx3.new_page()
    errors5 = []
    p5.on("pageerror", lambda e: errors5.append(str(e)))
    p5.on("console", lambda m: errors5.append(m.text) if m.type == "error" else None)
    p5.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), handler)
    p5.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), hist_handler)
    p5.goto("file://" + TEST_COPY)
    p5.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    DONE_JS = "document.getElementById('status').textContent.startsWith('Klaar') && !document.getElementById('scanBtn').disabled"
    VOL_JS = "document.getElementById('status').textContent.includes('Volume met data per plek') && !document.getElementById('scanBtn').disabled"

    p5.click("#scanBtn")
    p5.wait_for_function(DONE_JS, timeout=90000)
    p5.click("#modeCity")
    sw = find(p5, "Broadsword")
    check(sw and sw[0][1] == "Lymhurst → Martlock" and sw[0][6] == "14 / ?", "stad->stad: Martlock nog niet opgehaald (was %s)" % (sw[0][1:7] if sw else None))
    hist_seen.clear()
    p5.click("#volBtn")
    p5.wait_for_function(VOL_JS, timeout=30000)
    sw = find(p5, "Broadsword")
    check(len(hist_seen) == 1 and "Martlock" in hist_seen[0], "knop haalt alleen de ontbrekende volumes op (%d verzoek)" % len(hist_seen))
    check(sw and sw[0][6] == "14 / 1,7", "volume Lymhurst / Martlock = 14 / 1,7 (was %s)" % (sw[0][6] if sw else None))
    check(sw and sw[0][7] == "26.057", "winst/dag = 15.200 x 12/7 = 26.057 (was %s)" % (sw[0][7] if sw else None))
    tip = p5.get_attribute("#tbody tr:has-text('Broadsword') td:nth-child(7)", "title") or ""
    check("Martlock: gemiddeld 1,7 verkocht per dag (3 van 7 dagen met data)" in tip, "tooltip toont dekking: " + tip.replace("\n", " | ")[:140])
    p5.locator("#viewMarkt .results").screenshot(path=SHOT_DIR + "/volume.png")

    # nog eens klikken: alles zit al in het geheugen
    hist_seen.clear()
    p5.click("#volBtn")
    p5.wait_for_function(VOL_JS, timeout=30000)
    check(len(hist_seen) == 0, "tweede klik haalt niets opnieuw op")

    # filter op minimaal volume
    p5.fill("#minVol", "5")
    check(len(find(p5, "Broadsword")) == 0, "min. 5 per dag verbergt het zwaard (1,7 per dag)")
    p5.fill("#minVol", "100000")
    check(len(rows(p5)) == 0 and "verkocht per dag" in p5.inner_text("#empty"), "lege lijst legt het volumefilter uit")
    p5.fill("#minVol", "1")
    check(len(find(p5, "Broadsword")) == 1, "min. 1 per dag toont het zwaard weer")
    p5.fill("#minVol", "0")

    # sorteren op winst/dag
    p5.click("#thead th:nth-child(8)")
    check(p5.get_attribute("#thead th:nth-child(8)", "aria-sort") == "descending", "sorteren op winst/dag")
    p5.click("#thead th:nth-child(7)")
    check(p5.get_attribute("#thead th:nth-child(7)", "aria-sort") == "descending", "sorteren op verk./dag")

    # automatisch ophalen uitzetten
    p5.uncheck("#volAuto")
    hist_seen.clear()
    p5.evaluate("document.getElementById('status').textContent = ''")
    p5.click("#scanBtn")
    p5.wait_for_function(DONE_JS, timeout=90000)
    check(len(hist_seen) == 0 and "Volume" not in p5.inner_text("#status"), "zonder auto-volume geen history-verzoeken")
    p5.reload()
    check(not p5.is_checked("#volAuto"), "auto-volume-instelling bewaard na herladen")
    check(not errors5, "geen JS-fouten bij volume: %s" % errors5)

    # history-API faalt: lijst blijft bruikbaar, duidelijke melding
    ctx4 = browser.new_context(locale="nl-NL", viewport={"width": 1400, "height": 900})
    p6 = ctx4.new_page()
    p6.route(re.compile(r"albion-online-data\.com/api/v2/stats/prices/"), handler)
    p6.route(re.compile(r"albion-online-data\.com/api/v2/stats/history/"), lambda r: r.fulfill(status=400, body="bad"))
    p6.goto("file://" + TEST_COPY)
    p6.evaluate("(() => { const c = document.getElementById('premium'); if(!c.checked){ c.click(); } })()")
    p6.click("#scanBtn")
    p6.wait_for_function("document.getElementById('status').textContent.includes('mislukt') && !document.getElementById('scanBtn').disabled", timeout=60000)
    check(len(find(p6, "Broadsword")) == 1, "prijslijst blijft staan als de history-API faalt")
    check(p6.is_enabled("#scanBtn") and not p6.is_enabled("#stopBtn"), "knoppen hersteld na mislukt volume")

    browser.close()

print("\nFAILURES:", len(failures))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
