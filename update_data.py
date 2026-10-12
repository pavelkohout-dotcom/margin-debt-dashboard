#!/usr/bin/env python3
"""Stáhne data z FINRA a FRED, spočítá ukazatele a zapíše data.json pro index.html.

Zdroje
  - FINRA Margin Statistics (xlsx, měsíčně od 1997): margin debt
  - FRED NASDAQ100 (csv, denně od 1986): index Nasdaq-100

Co se počítá (stejně jako v původním skriptu plot_margin_debt.py)
  - yoy      meziroční změna margin debt v %
  - excess   yoy minus meziroční změna Nasdaq-100 (k poslednímu dni měsíce)
  - pct      percentil 0-100 = průměr percentilů z yoy a excess; každý percentil
             se počítá jen vůči PŘEDCHOZÍM měsícům (bez pohledu do budoucnosti),
             platí až po 60 měsících historie, tedy od 12/2002
  - dist     vzdálenost Nasdaq-100 od 200denního klouzavého průměru v %

Spuštění:  python update_data.py [--force]

Skript je určený pro denní běh v GitHub Actions (.github/workflows/update.yml),
ale funguje i lokálně. Pokud se data od minulého běhu nezměnila, data.json
nepřepíše, takže v gitu nevznikají prázdné commity (--force vynutí přepis).
Při chybě (zdroj nedostupný, změněný formát, zastaralá data) skončí s nenulovým
kódem a data.json nechá beze změny, takže web dál ukazuje poslední platná data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "data.json"

# Pokud FINRA změní adresu souboru, najdeš odkaz na xlsx na stránce
# https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics
FINRA_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQ100"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124 Safari/537.36")

MIN_MONTHS = 60        # minimální historie pro percentil (5 let)
SMA_DAYS = 200         # délka klouzavého průměru
START = "1998-01-01"   # začátek zobrazených řad

# Kontroly čerstvosti: margin debt se zveřejňuje s odstupem několika týdnů,
# proto je povolené stáří vyšší než u denního indexu.
MAX_MARGIN_AGE_DAYS = 100
MAX_NDX_AGE_DAYS = 10


def http_get(url: str) -> bytes:
    """Stáhne url. Zkouší výchozí User-Agent urllib (FRED zahazuje spojení
    s prohlížečovým UA) a pak prohlížečový; 3 pokusy s rostoucí pauzou."""
    errors: list[str] = []
    for attempt in range(3):
        for ua in (None, BROWSER_UA):
            req = urllib.request.Request(url, headers={"User-Agent": ua} if ua else {})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Nepodařilo se stáhnout {url}: " + " | ".join(errors[-2:]))


def expanding_pct(s: pd.Series, min_periods: int) -> pd.Series:
    """Percentil (0-100) vůči všem dosavadním hodnotám; bez look-ahead biasu."""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i, v in enumerate(vals):
        if np.isnan(v):
            continue
        hist = vals[: i + 1]
        hist = hist[~np.isnan(hist)]
        if len(hist) >= min_periods:
            out[i] = 100.0 * ((hist < v).sum() + 0.5 * (hist == v).sum()) / len(hist)
    return pd.Series(out, index=s.index)


def load_margin(today: pd.Timestamp) -> pd.DataFrame:
    x = pd.read_excel(io.BytesIO(http_get(FINRA_URL)))
    if x.shape[1] < 2:
        raise RuntimeError("FINRA xlsx má neočekávaný formát (méně než 2 sloupce)")
    ym = x.iloc[:, 0].astype(str).str.strip()
    month_end = pd.to_datetime(ym + "-01", errors="coerce") + pd.offsets.MonthEnd(0)
    m = pd.DataFrame({
        "month_end": month_end,
        "debit": pd.to_numeric(x.iloc[:, 1], errors="coerce"),   # mil. USD
    }).dropna().sort_values("month_end").set_index("month_end")
    if len(m) < 300:
        raise RuntimeError(f"FINRA: načteno jen {len(m)} měsíců, očekáváno aspoň 300 "
                           "(změnil se formát souboru?)")
    age = (today - m.index[-1]).days
    if age > MAX_MARGIN_AGE_DAYS:
        raise RuntimeError(f"FINRA: poslední měsíc je {m.index[-1]:%Y-%m}, "
                           f"tedy {age} dní starý (limit {MAX_MARGIN_AGE_DAYS})")
    return m


def load_ndx(today: pd.Timestamp) -> pd.Series:
    f = pd.read_csv(io.BytesIO(http_get(FRED_URL)))
    ndx = pd.Series(pd.to_numeric(f.iloc[:, 1], errors="coerce").to_numpy(),
                    index=pd.to_datetime(f.iloc[:, 0])).dropna().sort_index()
    if len(ndx) < 5000:
        raise RuntimeError(f"FRED: načteno jen {len(ndx)} dní, očekáváno aspoň 5000")
    age = (today - ndx.index[-1]).days
    if age > MAX_NDX_AGE_DAYS:
        raise RuntimeError(f"FRED: poslední den je {ndx.index[-1]:%Y-%m-%d}, "
                           f"tedy {age} dní starý (limit {MAX_NDX_AGE_DAYS})")
    return ndx


def compute(m: pd.DataFrame, ndx: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    m = m.copy()
    m["yoy"] = m["debit"].pct_change(12) * 100
    ndx_yoy = ndx.reindex(m.index, method="ffill").pct_change(12) * 100
    m["excess"] = m["yoy"] - ndx_yoy
    m["yoy_pct"] = expanding_pct(m["yoy"], MIN_MONTHS)
    m["excess_pct"] = expanding_pct(m["excess"], MIN_MONTHS)
    m["pct"] = m[["yoy_pct", "excess_pct"]].mean(axis=1, skipna=False)

    sma = ndx.rolling(SMA_DAYS).mean()
    d = pd.DataFrame({"ndx": ndx, "dist": (ndx / sma - 1) * 100}).dropna()
    return m.loc[START:], d.loc[START:]


def num(v, nd: int = 4):
    """float -> zaokrouhlené číslo, NaN -> None (null v JSON).

    Čtyři desetinná místa stačí na to, aby se hodnota po zaokrouhlení na jedno
    místo v prohlížeči shodovala s výpočtem v Pythonu (dvojí zaokrouhlení na 2 a
    pak na 1 místo by u hodnot jako 98,249 dalo 98,3 místo 98,2)."""
    if v is None or not np.isfinite(v):
        return None
    return round(float(v), nd)


def build_payload(m: pd.DataFrame, d: pd.DataFrame) -> dict:
    return {
        "version": 1,
        "sources": {
            "margin_debt": "FINRA Margin Statistics (debit balances in customers' "
                           "securities margin accounts, mil. USD)",
            "ndx": "FRED NASDAQ100 (Nasdaq-100 Index)",
        },
        "params": {"min_months": MIN_MONTHS, "sma_days": SMA_DAYS},
        "asof": {"margin_month": f"{m.index[-1]:%Y-%m-%d}", "ndx": f"{d.index[-1]:%Y-%m-%d}"},
        "monthly": {
            "t": [f"{t:%Y-%m-%d}" for t in m.index],
            "debit": [int(v) for v in m["debit"]],
            "yoy": [num(v) for v in m["yoy"]],
            "excess": [num(v) for v in m["excess"]],
            "pct": [num(v) for v in m["pct"]],
        },
        "daily": {
            "t": [f"{t:%Y-%m-%d}" for t in d.index],
            "ndx": [num(v) for v in d["ndx"]],
            "dist": [num(v) for v in d["dist"]],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="přepsat data.json i beze změny dat")
    args = ap.parse_args()

    today = pd.Timestamp(dt.datetime.now(dt.timezone.utc).date())
    m, ndx = load_margin(today), load_ndx(today)
    m, d = compute(m, ndx)
    payload = build_payload(m, d)

    last_yoy, last_pct, last_dist = m["yoy"].iloc[-1], m["pct"].iloc[-1], d["dist"].iloc[-1]
    if not (np.isfinite(last_pct) and np.isfinite(last_yoy) and np.isfinite(last_dist)):
        raise RuntimeError("Poslední hodnoty nejsou číselné, výpočet selhal")
    print(f"Margin debt {m.index[-1]:%Y-%m}: percentil {last_pct:.1f}, "
          f"meziroční změna {last_yoy:.1f} %")
    print(f"Nasdaq-100 {d.index[-1]:%Y-%m-%d}: {last_dist:.1f} % od 200denního průměru")

    if OUT.exists() and not args.force:
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            old.pop("generated", None)
            if old == payload:
                print("Data se od minulého běhu nezměnila, data.json zůstává.")
                return 0
        except json.JSONDecodeError:
            pass  # poškozený soubor přepíšeme

    payload["generated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"Zapsáno: {OUT.name} ({OUT.stat().st_size / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"CHYBA: {exc}", file=sys.stderr)
        sys.exit(1)
