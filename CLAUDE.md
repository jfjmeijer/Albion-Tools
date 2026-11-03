# Albion Tools – notities voor Claude

Site van Joris (GitHub Pages vanaf `main`, map `/`): https://jfjmeijer.github.io/Albion-Tools/
UI-tekst is Nederlands. Joris wil korte, scanbare uitleg.

## Werkwijze
- Wijzig `src/template.html` (en zo nodig `tools/build_data.py`), bouw daarna `index.html`:
  `python3 -I tools/build_data.py <items.txt> <items.json> src/template.html index.html`
  (items.txt en items.json uit github.com/ao-data/ao-bin-dumps, map `formatted/`; niet committen).
- Test: `python3 -I tests/test_ui.py index.html /tmp/copy.html /tmp` en `tests/test_site.py` (zelfde argumenten). De tests verwachten items.json op `/home/claude/dl/itemsjson/items.json`; pas dat pad aan als het ergens anders staat.
- Nieuwe tool = nieuwe tab: een `<main id="view…">`, een knop in `.tabs` (data-view) en een sleutel in `VIEWS`.

## Belangrijk
- Logboek staat in `localStorage` onder `albionLogboek.v1` (`{entries,start,goal}`); wijzig dat formaat nooit zonder migratie, anders is Joris zijn logboek kwijt.
- Instellingen: `albionFlipToolSettings.v1`.
- API: `/api/v2/stats/prices/…` en `/api/v2/stats/history/…?time-scale=24`, ±1 verzoek per 1,1 s (limiet 180/min, 300/5 min), URL ≤ 3600 tekens.
- Belasting 8% zonder premium (Joris heeft geen premium), 4% met; setup fee 2,5% alleen bij orders.
- Er bestaat ook een claude.ai-artifact "Albion Tools" (slug `0f4ca3c5-cb4c-4881-af74-bc86eaed87de`) met online logboek-opslag; daar werkt live scannen niet.
