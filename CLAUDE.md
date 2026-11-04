# Albion Tools – notities voor Claude

Site van Joris (GitHub Pages vanaf `main`, map `/`): https://jfjmeijer.github.io/Albion-Tools/
UI-tekst is Nederlands. Joris wil korte, scanbare uitleg.

## Werkwijze
- Wijzig `src/template.html` (en zo nodig `tools/build_data.py`), bouw daarna `index.html`:
  `python3 -I tools/build_data.py <items.txt> <items.json> src/template.html index.html`
  (items.txt en items.json uit github.com/ao-data/ao-bin-dumps, map `formatted/`; niet committen).
- Test: `python3 -I tests/test_ui.py index.html /tmp/copy.html /tmp` en `tests/test_site.py` (zelfde argumenten). De tests verwachten items.json op `/home/claude/dl/itemsjson/items.json`; pas dat pad aan als het ergens anders staat.
- Tabs nu: Markt (modi BM, Stad → stad, Routes, Buy orders, Craften; Laadplanner boven de resultaten), Orders, Mandje, Logboek.
- Craftbonussen per stad: `tools/craftingbonus.json` (uit ao-bin-dumps `craftingmodifiers.json`, clusters 0000 Thetford, 1000 Lymhurst, 2000 Bridgewatch, 3004 Martlock, 4000 Fort Sterling, 3003 Caerleon, 5000 Brecilien). RRR = 1 − 1/(1 + basis 0,18 + bonus itemsoort + focus 0,59). Items krijgen `cc` (craftingcategory).
- Nieuwe tool = nieuwe tab: een `<main id="view…">`, een knop in `.tabs` (data-view) en een sleutel in `VIEWS`.

## Eigen data (privé)
- `helper/` = Go-hulpprogramma (`main.go`), luistert op `127.0.0.1:47800`. De Albion Data Client wordt gestart met `-i http://127.0.0.1:47800` (dus niets naar AODP; `noop` NIET gebruiken, dat laat de client crashen). Endpoints: `POST /marketorders.ingest` (zelfde JSON als lib.MarketOrder; prijzen ×10.000), `GET /status`, `GET /prices?since=<ms>` (AODP-achtige records + `sell_amount`/`buy_amount`, `source:"local"`).
- Bouwen: `cd helper && GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o albion-helper.exe .` en daarna `albion-tools-helper.zip` opnieuw maken (exe + `start-albion-prive.bat` + `LEESMIJ.txt`). De losse exe staat in .gitignore; de zip wordt gecommit.
- Locatie-ID's: 0007 Thetford, 1002 Lymhurst, 2004 Bridgewatch, 3008 Martlock, 4002 Fort Sterling, 3005 Caerleon, 5003 Brecilien, 3003 of `*-Auction2` = Black Market (nog niet live geverifieerd; onbekende ID's toont de site in de status).
- Site: instelling `S.local`, polling elke 15 s; `ingest()` laat de nieuwste prijs winnen.
- `S.useAvg`: verkoopprijs = 7-daags gemiddelde uit de history (`avgOf`), alleen als die voor de verkoopplek bekend is (rijen krijgen `sellAvg`).

## Belangrijk
- Logboek staat in `localStorage` onder `albionLogboek.v1` (`{entries,start,goal}`); wijzig dat formaat nooit zonder migratie, anders is Joris zijn logboek kwijt.
- Instellingen: `albionFlipToolSettings.v1`. Mandje + afvinklijst: `albionMandje.v1` (`{items,stage,checks}`; items zijn snapshots van scanrijen). Gevolgde orders: `albionOrders.v1` (`{list,checked}`).
- API: `/api/v2/stats/prices/…` en `/api/v2/stats/history/…?time-scale=24`, ±1 verzoek per 1,1 s (limiet 180/min, 300/5 min), URL ≤ 3600 tekens.
- Belasting 8% zonder premium (Joris heeft geen premium), 4% met; setup fee 2,5% alleen bij orders.
- Er bestaat ook een claude.ai-artifact "Albion Tools" (slug `0f4ca3c5-cb4c-4881-af74-bc86eaed87de`) met online logboek-opslag; daar werkt live scannen niet.
