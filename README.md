# Margin debt a Nasdaq-100

Interaktivní stránka se třemi grafy (percentil margin debt, jeho meziroční změna a vzdálenost Nasdaq-100 od 200denního průměru). Data se denně přepočítávají v GitHub Actions a stránka se nasazuje na GitHub Pages.

## Soubory

| Soubor | K čemu je |
|---|---|
| `index.html` | stránka s grafy, načítá `data.json`, žádné závislosti |
| `data.json` | spočítaná data, generuje `update_data.py` |
| `update_data.py` | stáhne FINRA a FRED, spočítá ukazatele, zapíše `data.json` |
| `.github/workflows/update.yml` | denní běh, commit změněných dat, nasazení na Pages |
| `requirements.txt` | pandas, numpy, openpyxl |

## Zprovoznění na GitHubu

1. Vytvoř repozitář (výchozí větev `main`) a nahraj do něj celý obsah této složky včetně skryté složky `.github`.
   Pokud ji web nahrávání nepřijme, vytvoř soubor přes *Add file → Create new file* s názvem `.github/workflows/update.yml` a vlož jeho obsah.
2. V repozitáři otevři *Settings → Pages* a jako *Source* vyber **GitHub Actions**.
3. Otevři záložku *Actions → Aktualizace dat a nasazení → Run workflow*. Po doběhnutí je stránka na `https://<uživatel>.github.io/<repozitář>/`.
   Pokud první běh po nahrání selže na kroku nasazení, protože Pages ještě nebyly zapnuté, spusť workflow znovu po kroku 2.

Dál už běží samo každý den. Když se data nezmění (víkend, svátek), nevznikne žádný commit.

## Lokální spuštění

```
pip install -r requirements.txt
python update_data.py          # přepočítá data.json
python -m http.server 8000     # pak otevři http://localhost:8000
```

Stránka se nedá otevřít dvojklikem na `index.html`, protože prohlížeč pak zakáže načtení `data.json`.

## Co dělat, když se něco rozbije

- **Workflow skončí chybou:** web zůstane na posledních platných datech a GitHub pošle e-mail. V logu kroku *Stažení a výpočet dat* je důvod. Skript záměrně selže, když zdroj nejde stáhnout, má neočekávaný formát nebo jsou data zastaralá (FINRA starší než 100 dní, FRED starší než 10 dní).
- **FINRA změnila adresu souboru:** nový odkaz na xlsx najdeš na stránce [FINRA Margin Statistics](https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics), uprav `FINRA_URL` v `update_data.py`.
- **Stránka hlásí, že jsou data stará:** zkontroluj záložku *Actions*. GitHub vypíná plánované běhy ve veřejných repozitářích, ve kterých se 60 dní nic neděje; běh stačí znovu zapnout.
- **Vložení do Wixu (pkohout.com):** použij prvek pro vložení cizího webu (iframe, ve Wixu „Embed a site“) a zadej do něj adresu stránky z GitHub Pages.

## Jak se to počítá

- `yoy`: meziroční změna margin debt (FINRA, mil. USD) v %.
- `excess`: `yoy` minus meziroční změna Nasdaq-100 k poslednímu dni měsíce.
- `pct`: průměr percentilu `yoy` a percentilu `excess`. Každý percentil se počítá jen vůči předchozím měsícům, takže hodnota z minulosti se zpětně nemění, a platí až po 60 měsících historie (od 12/2002).
- `dist`: Nasdaq-100 děleno jeho 200denním klouzavým průměrem minus 1, v %. Používá se index z FRED místo ETF QQQ kvůli delší historii; od roku 2016 se obě řady shodují.

FINRA údaje občas zpětně opravuje. Skript proto při každém běhu počítá celou historii znovu.
