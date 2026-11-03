# Albion Tools

Persoonlijke Albion Online-tools: flippen naar de Black Market, stad → stad, handelsroutes (winst per dag en per kg), craften en refinen, plus een logboek voor winst en uitgaven met grafieken.

**Site:** https://jfjmeijer.github.io/Albion-Tools/

- Prijzen en verkoopvolumes komen live van het [Albion Online Data Project](https://www.albion-online-data.com/) (Europa, Amerika of Azië).
- Het logboek wordt in je browser op deze site bewaard en blijft staan als de site wordt bijgewerkt. Maak af en toe een back-up met "Exporteer JSON".

## Opbouw

| Pad | Wat |
| --- | --- |
| `index.html` | De gebouwde site (één bestand, itemdata ingebakken). |
| `src/template.html` | Bron van de site; `__ITEMDATA__` wordt bij het bouwen vervangen. |
| `tools/build_data.py` | Bouwt `index.html` uit `items.txt` en `items.json` van [ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps). |
| `tests/` | Playwright-tests met nagebootste API-antwoorden. |

Bouwen:

```sh
python3 -I tools/build_data.py items.txt items.json src/template.html index.html
```
