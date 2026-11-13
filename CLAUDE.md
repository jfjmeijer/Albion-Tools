# Albion Tools – notities voor Claude

Site van Joris (GitHub Pages vanaf `main`, map `/`): https://jfjmeijer.github.io/Albion-Tools/
UI-tekst is Nederlands. Joris wil korte, scanbare uitleg.

## Werkwijze
- Wijzig `src/template.html` (en zo nodig `tools/build_data.py`), bouw daarna `index.html`:
  `python3 -I tools/build_data.py <items.txt> <items.json> src/template.html index.html`
  (items.txt en items.json uit github.com/ao-data/ao-bin-dumps, map `formatted/`; niet committen).
- Test: `python3 -I tests/test_ui.py index.html /tmp/copy.html /tmp` en `tests/test_site.py` (zelfde argumenten). De tests verwachten items.json op `/home/claude/dl/itemsjson/items.json`; pas dat pad aan als het ergens anders staat.
- Tabs nu: Markt (modi BM, Stad → stad, Routes, Buy orders, Craften; Laadplanner boven de resultaten), Favorieten, Orders, Mandje, Logboek.
- Reisroute (tab `reis`): zonegraaf in `tools/world.json`, gemaakt met `python3 -I tools/build_world.py <cluster/world.xml uit ao-bin-dumps> tools/world.json`; ingebakken via `__WORLD__`. Richting = hoek tussen `worldmapposition` van twee zones (y omhoog = noord). Opslag `albionReis.v1`. Roads of Avalon (TUNNEL_*) zitten er niet in.
- Favorieten (`albionFavorieten.v1`, `{list,batches,checked}`; batches = `{id,name,items:[spec+qty],saved,lastProfit}`, gemaakt uit het mandje; mandje-items dragen `spec` met de instellingen): elke favoriet bewaart modus, route en instellingen van toen; `evalFav` rekent hem door via `withSettings` + computeFlips/computeCrafts.
- Craftbonussen per stad: `tools/craftingbonus.json` (uit ao-bin-dumps `craftingmodifiers.json`, clusters 0000 Thetford, 1000 Lymhurst, 2000 Bridgewatch, 3004 Martlock, 4000 Fort Sterling, 3003 Caerleon, 5000 Brecilien). RRR = 1 − 1/(1 + basis 0,18 + bonus itemsoort + focus 0,59). Items krijgen `cc` (craftingcategory).
- Inzicht (tab `inzicht`, `albionInzicht.v1`): goudprijs (`/api/v2/stats/gold.json?count=720`), premium-check (omzet uit logregels met `rev`), winst per uur (logregels met `min`). Logregels hebben optioneel `min` en `rev` (compatibel met het oude formaat).
- Prijsalarm: `FAV.alarm = {on, every}` + `alarm` per favoriet (winst per stuk) of batch (totaal); `alarmEval()` na Check alle en na eigen data; melding via Notification + toast.
- Craftmaterialen: `S.matMode` = `cheapest` (per materiaal, uit `S.matCities`), `onecity` (goedkoopste stad voor het hele recept), `craftcity` (bonusstad van het item), of een vaste stad; `matLocs()` bepaalt waar materialen worden opgehaald. Zit ook in `spec` van favorieten/batches.
- Handmatig toevoegen (Mandje, `#manualBox`): bouwt een spec met `currentSpec()`, haalt prijzen op via `fetchForSpecs([spec])` en rekent met `evalFav`; cart-key begint met `manual|`. `evalManual` negeert `S.maxAge` (waarschuwt bij oude prijs) en zet eigen prijzen (`#mBuy`/`#mSell`) tijdelijk in `store`; `manualParse` zoekt ook los ("t6.1 glacial staff").
- Spec-tree: items krijgen `st` (shopsubcategory1 uit items.json; verzamelgear `gather_<soort>`, capes/bags samengevoegd). `S.tree` filtert in `artOk`; met een tree tellen de itemsoort-knoppen niet (`catOn`). Labels in `TREE_LABELS`; `evalFav` zet `tree:""`.
- Prijsleeftijd: `S.maxAge` (steden) en `S.maxAgeBm` (0 = zelfde; anders aparte grens voor de BM). `freshFn()` geeft `fresh(t, loc)`; geef altijd de plek mee.
- Craften leeg? `computeCrafts()` geeft `diag` (items, noSell, noMat + matMiss, noOrder = BM-sell order ontbreekt, sellOld, below, best); `craftWhy()` toont dat in `#empty`.
- Route-overlay (tab Reisroute, `#trOverlay`): Document Picture-in-Picture (fallback `window.open`), pollt elke 2 s `GET /location` van de helper (1.2+). Voortgang = `TRAVEL.checks` (`routeIdx`/`setRouteIdx`); `locToZone` zet markt-/portal-ID's om naar de stad; buiten de route → `TRAVEL.from` = huidige zone (nieuwe route).
- Uitschieters: rij krijgt `outlier` als de verkoopprijs > 7d-gem. × (1 + `S.outPct`); `S.hideOut` verbergt ze.
- Nieuwe tool = nieuwe tab: een `<main id="view…">`, een knop in `.tabs` (data-view) en een sleutel in `VIEWS`.

## Eigen data (privé)
- `helper/` = Go-hulpprogramma (`main.go`), luistert op `127.0.0.1:47800`. De Albion Data Client wordt gestart met `-i http://127.0.0.1:47800` (dus niets naar AODP; `noop` NIET gebruiken, dat laat de client crashen). Endpoints: `POST /marketorders.ingest` (zelfde JSON als lib.MarketOrder; prijzen ×10.000), `POST /markethistories.ingest` (AlbionId → item-ID via `helper/items_index.txt`, gemaakt met `tools/gen_helper_items.py` uit items.txt; Timestamp = .NET-ticks; SilverAmount ×10.000), `GET /status`, `GET /prices?since=<ms>` (AODP-achtige records + `sell_amount`/`buy_amount`, `source:"local"`), `GET /history?since=<ms>` (`per_day`, `avg_price`, `days`, `timescale`), `GET /location` (`{id,t,logFound}`: leest "Updating player location to X" uit `albiondata-client.log` naast de helper — de client schrijft dat in zijn werkmap; andere plek: `-clientlog <pad>`).
- Bouwen: `cd helper && GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o albion-helper.exe .` en daarna `albion-tools-helper.zip` opnieuw maken (exe + `start-albion-prive.bat` + `LEESMIJ.txt`). De losse exe staat in .gitignore; de zip wordt gecommit.
- Mac: `GOOS=darwin GOARCH=arm64` → `albion-helper-mac-arm64` en `GOARCH=amd64` → `albion-helper-mac-intel`, samen met `start-albion-prive.command` (kiest binary via `uname -m`, zoekt de client of vraagt het pad → `client-pad.txt`, maakt eerst /dev/bpf0-63 aan (macOS kloont ze anders later met root-rechten) en zet ze op groep admin + g+rw (libpcap opent lezen+schrijven, één apparaat per interface) en start de client als gewone gebruiker met `-i http://127.0.0.1:47800`; vindt ook `Albion Data Client.app`) en `LEESMIJ-mac.txt` in `albion-tools-helper-mac.zip`. Script en leesmij staan ook los in helper/; losse Mac-binaries niet committen.
- Locatie-ID's: 0007 Thetford, 1002 Lymhurst, 2004 Bridgewatch, 3008 Martlock, 4002 Fort Sterling, 3005 Caerleon, 5003 Brecilien, 3003 of `*-Auction2` = Black Market (nog niet live geverifieerd; onbekende ID's toont de site in de status).
- Site: instelling `S.local`, polling elke 15 s; `ingest()` laat de nieuwste prijs winnen. Eigen history komt in `volStore` met `local:true` en wordt niet door openbare history overschreven.
- `S.useAvg`: verkoopprijs = 7-daags gemiddelde uit de history (`avgOf`), alleen als die voor de verkoopplek bekend is (rijen krijgen `sellAvg`).

## Belangrijk
- Logboek staat in `localStorage` onder `albionLogboek.v1` (`{entries,start,goal}`); wijzig dat formaat nooit zonder migratie, anders is Joris zijn logboek kwijt.
- Instellingen: `albionFlipToolSettings.v1`. Mandje + afvinklijst: `albionMandje.v1` (`{items,stage,checks}`; items zijn snapshots van scanrijen). Gevolgde orders: `albionOrders.v1` (`{list,checked}`).
- API: `/api/v2/stats/prices/…` en `/api/v2/stats/history/…?time-scale=24`, ±1 verzoek per 1,1 s (limiet 180/min, 300/5 min), URL ≤ 3600 tekens.
- Verkopen aan de Black Market: aparte keuze `S.bmSell` (`instant` = aan BM-buy orders, `order` = sell order op de BM tegen de laagste BM sell order of het 7d-gemiddelde). Spelers kunnen op de BM sell orders zetten; de prijsgrafiek zie je alleen in het verkoopvenster (item moet in je inventaris).
- Belasting 8% zonder premium (Joris heeft geen premium), 4% met; setup fee 2,5% alleen bij orders.
- Er bestaat ook een claude.ai-artifact "Albion Tools" (slug `0f4ca3c5-cb4c-4881-af74-bc86eaed87de`) met online logboek-opslag; daar werkt live scannen niet.
