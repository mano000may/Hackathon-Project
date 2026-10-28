"""Data + model layer. Free and local: prices come from yfinance once, then live in data/*.csv."""
import json
import time
import warnings
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import StringIO
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_PATH = BASE_DIR / "model" / "pooled_model.joblib"
QUALITY_PATH = DATA_DIR / "quality.json"
FRED_PATH = DATA_DIR / "fred.csv"
SHARES_PATH = DATA_DIR / "shares.csv"
SITES_PATH = DATA_DIR / "websites.csv"
LOGO_DIR = DATA_DIR / "logos"
PRED_PATH = DATA_DIR / "predictions.json"
LOGO_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico", "image/svg+xml": "svg", "image/webp": "webp"}
DATA_DIR.mkdir(exist_ok=True)
MODEL_PATH.parent.mkdir(exist_ok=True)

H = 5  # prediction horizon in trading days; 5-day returns are less noisy than 1-day
MARKET = ["SPY"]  # tested yields/VIX/credit/gold/dollar too: they did not improve out-of-sample results, so dropped
STOCK_FEATS = ['ret_1', 'ret_5', 'ret_10', 'ret_20', 'ret_60', 'ma_5', 'ma_10', 'ma_20', 'rsi_14', 'vol_chg', 'vol_20']
OWN_COLS = STOCK_FEATS + ['rel_5']  # rel_5: 5-day return minus the market's; computable from one stock + SPY
CROSS_FEATS = ['sec_rel_5', 'rank_ret_20']  # need every stock on the same day: vs sector peers, rank in universe
FRED_SERIES = {  # id: label. Free CSVs from FRED, no API key. Daily series only (monthly ones have release-date look-ahead)
    'DGS10': '10-Year Treasury Yield', 'T10Y2Y': 'Yield Curve (10Y Minus 2Y)',
    'BAA10Y': 'Corporate Credit Spread', 'DFF': 'Fed Funds Rate'}
FRED_FEATS = ['dgs10', 'curve', 'credit', 'fed', 'dgs10_chg20', 'credit_chg20']
FEATURES = OWN_COLS + CROSS_FEATS + FRED_FEATS
VOL_OWN = ['lv5', 'lv20', 'lv60', 'lvabs']  # log realised volatility over 5/20/60 days and today's absolute move (HAR-style inputs)
VOL_FEATS = VOL_OWN + ['mkt_lv20', 'rel_lv']  # + market-wide volatility and this stock's volatility relative to it
FALLBACK_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX']
TICKER_CACHE_DAYS = 30
MAX_STALE_DAYS = 7  # a stock whose last bar is older than this (vs the newest bar anywhere) is left out
CHUNK = 100

_model_cache = {}
_pred_cache = {}


def _csv(symbol):
    return DATA_DIR / f"{symbol.replace('^', '_')}.csv"


def _ticker_table():
    cache = DATA_DIR / "tickers.csv"
    old = (not cache.exists() or time.time() - cache.stat().st_mtime > TICKER_CACHE_DAYS * 86400
           or 'GICS Sector' not in pd.read_csv(cache, nrows=0).columns)
    if old:
        try:
            r = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
            t = pd.read_html(StringIO(r.text))[0]
            t['Symbol'] = t['Symbol'].str.replace('.', '-', regex=False)
            t[['Symbol', 'Security', 'GICS Sector']].to_csv(cache, index=False)
        except Exception as e:
            print(f"Error fetching tickers: {e}")
    if cache.exists():
        return pd.read_csv(cache)
    return pd.DataFrame({'Symbol': FALLBACK_TICKERS, 'Security': FALLBACK_TICKERS})


def get_tickers():
    """[(symbol, name)] for the whole S&P 500 (local CSV, refreshed from Wikipedia monthly)."""
    t = _ticker_table()
    return list(zip(t['Symbol'], t['Security']))


def get_sectors():
    t = _ticker_table()
    return dict(zip(t['Symbol'], t.get('GICS Sector', pd.Series('Unknown', index=t.index))))


def clean_prices(df):
    """Drop corrupt rows (duplicates, non-positive prices, high < low) before they reach the model."""
    df = df[~df.index.duplicated(keep='last')].sort_index().dropna(subset=['Close'])
    df = df[(df['Close'] > 0) & (df['High'] >= df['Low'])].copy()
    df['Volume'] = df['Volume'].fillna(0).astype('int64')
    return df


def save_prices(symbol, df):
    df.to_csv(_csv(symbol), float_format="%.4f")  # 4 decimals: raw 15-digit floats doubled file size and load time


def refresh_prices(symbols, log=print):
    """Download 10y of adjusted prices (stocks + macro proxies) in chunks into data/. Returns an error string or None."""
    import yfinance as yf  # imported here: only updates need it, and it adds ~1s to every startup
    syms = list(dict.fromkeys(list(symbols) + MARKET))
    failed = []
    for i in range(0, len(syms), CHUNK):
        chunk, data = syms[i:i + CHUNK], None
        for _ in range(2):  # one retry per chunk
            try:
                data = yf.download(chunk, period="10y", interval="1d", progress=False,
                                   auto_adjust=True, group_by="ticker", threads=True)
                break
            except Exception:
                time.sleep(2)
        if data is None:
            failed += chunk
            continue
        for s in chunk:
            try:
                df = clean_prices(data[s])
                if df.empty:
                    failed.append(s)
                else:
                    save_prices(s, df)
            except Exception:
                failed.append(s)  # keeps the previous CSV for this symbol
        log(f"Downloaded {min(i + CHUNK, len(syms))}/{len(syms)}")
    if len(failed) == len(syms):
        return "Download failed (offline?)"
    quality_report(symbols, failed)
    return f"{len(failed)} symbols failed, kept old data" if failed else None


def load_prices(symbol):
    path = _csv(symbol)
    return pd.read_csv(path, index_col=0, parse_dates=True) if path.exists() else None


def quality_report(symbols, failed=()):
    """Spot data problems: stale series, gaps, absurd one-day moves. Saved to data/quality.json."""
    last, issues = {}, {}
    for s in symbols:
        df = load_prices(s)
        if df is None:
            issues[s] = ['no data']
            continue
        last[s] = df.index.max()
        gaps = int((df.index.to_series().diff().dt.days > 7).sum())
        jumps = int((df['Close'].pct_change().abs() > 0.4).sum())
        found = [f'{gaps} gaps over a week'] * (gaps > 0) + [f'{jumps} one-day moves over 40%'] * (jumps > 0)
        if found:
            issues[s] = found
    newest = max(last.values()) if last else None
    for s, d in last.items():
        if (newest - d).days > MAX_STALE_DAYS:
            issues.setdefault(s, []).append(f'stale since {d:%Y-%m-%d}, excluded')
    rep = {'generated': date.today().isoformat(), 'newest_bar': newest.strftime('%Y-%m-%d') if newest is not None else None,
           'stocks': len(last), 'download_failed': sorted(failed), 'issues': issues}
    QUALITY_PATH.write_text(json.dumps(rep, indent=1))
    return rep


def quality_summary():
    if not QUALITY_PATH.exists():
        return None
    r = json.loads(QUALITY_PATH.read_text())
    return {k: r[k] for k in ('generated', 'newest_bar', 'stocks')} | {
        'flagged': len(r['issues']), 'failed': len(r['download_failed']), 'issues': r['issues']}


def refresh_shares(symbols, log=print):
    """Shares outstanding per stock (changes slowly). Market cap is shares x latest close, computed locally."""
    import yfinance as yf

    def one(sym):
        try:
            return sym, yf.Ticker(sym).fast_info.get('shares')
        except Exception:
            return sym, None
    with warnings.catch_warnings(), ThreadPoolExecutor(8) as ex:
        warnings.simplefilter('ignore')  # yfinance prints a pandas deprecation line per symbol
        got = {sym: n for sym, n in ex.map(one, symbols) if n}
    old = pd.read_csv(SHARES_PATH, index_col=0)['Shares'].to_dict() if SHARES_PATH.exists() else {}
    merged = old | got  # failures keep their previous value
    if not merged:
        return "Share counts download failed (offline?)"
    pd.Series(merged, name='Shares').rename_axis('Symbol').to_csv(SHARES_PATH)
    log(f"Updated share counts for {len(got)} stocks")
    return None if len(got) == len(list(symbols)) else f"{len(list(symbols)) - len(got)} share counts failed, kept old values"


def load_shares():
    return pd.read_csv(SHARES_PATH, index_col=0)['Shares'].to_dict() if SHARES_PATH.exists() else {}


LOGO_MAGIC = {"png": b"\x89PNG", "jpg": b"\xff\xd8", "ico": b"\x00\x00\x01\x00", "webp": b"RIFF", "svg": b"<"}


def _valid_logo(f):
    """A truncated or HTML-error download must never reach an <img>, so check the file signature."""
    try:
        head = f.read_bytes()[:8]
    except OSError:
        return False
    return f.stat().st_size > 150 and head.startswith(LOGO_MAGIC.get(f.suffix[1:], b"\0\0"))


def logo_file(symbol):
    if not LOGO_DIR.exists():
        return None
    return next((f for f in LOGO_DIR.glob(f"{symbol}.*") if _valid_logo(f)), None)


def monogram_svg(symbol):
    """Local stand-in logo for companies without a favicon: initials on a colour derived from the symbol."""
    hue = sum(ord(c) * (i + 3) for i, c in enumerate(symbol)) % 360
    txt = symbol[:2] if len(symbol) > 1 and symbol[1].isalpha() else symbol[:1]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
            f'<rect width="64" height="64" rx="14" fill="hsl({hue} 38% 24%)"/>'
            f'<text x="32" y="33" text-anchor="middle" dominant-baseline="central" font-family="system-ui,sans-serif" '
            f'font-size="26" font-weight="700" fill="hsl({hue} 80% 88%)">{txt}</text></svg>')


def refresh_logos(symbols, log=print):
    """Fetch each company's favicon once (website from Yahoo, icon from Google's favicon service) and keep it on disk.
    Only missing logos are downloaded, so later updates are quick."""
    import yfinance as yf
    LOGO_DIR.mkdir(exist_ok=True)
    sites = pd.read_csv(SITES_PATH, index_col=0)['Domain'].fillna('').to_dict() if SITES_PATH.exists() else {}

    def domain(sym):
        if sym in sites:
            return sym, sites[sym]
        try:
            host = urlparse(yf.Ticker(sym).info.get('website') or '').hostname or ''
            return sym, host.removeprefix('www.')
        except Exception:
            return sym, ''

    def icon(item):
        sym, dom = item
        if not dom or logo_file(sym):
            return sym, None
        try:
            r = requests.get('https://t3.gstatic.com/faviconV2', timeout=10, params={
                'client': 'SOCIAL', 'type': 'FAVICON', 'fallback_opts': 'TYPE,SIZE,URL', 'url': f'https://{dom}', 'size': 128})
            ext = LOGO_EXT.get(r.headers.get('content-type', '').split(';')[0])
            if r.status_code == 200 and ext:  # 404 means no real favicon (Google would otherwise send a globe)
                f = LOGO_DIR / f"{sym}.{ext}"
                f.write_bytes(r.content)
                if _valid_logo(f):
                    return sym, True
                f.unlink()  # a 16px placeholder or non-image: the monogram looks better
        except Exception:
            pass
        return sym, False

    todo = [s for s in symbols if not logo_file(s)]
    with warnings.catch_warnings(), ThreadPoolExecutor(8) as ex:
        warnings.simplefilter('ignore')
        sites |= {k: v for k, v in ex.map(domain, todo)}
        got = sum(1 for _, ok in ex.map(icon, [(s, sites.get(s, '')) for s in todo]) if ok)
    pd.Series(sites, name='Domain').rename_axis('Symbol').to_csv(SITES_PATH)
    log(f"Downloaded {got} new logos ({sum(1 for s in symbols if logo_file(s))} of {len(list(symbols))} available)")
    return None


def refresh_fred(log=print):
    """One CSV request per series (FRED's multi-series download is a zip). Returns an error string or None."""
    cols, failed = {}, []
    for sid in FRED_SERIES:
        for _ in range(3):
            try:
                r = requests.get('https://fred.stlouisfed.org/graph/fredgraph.csv',
                                 params={'id': sid, 'cosd': '2015-01-01'}, timeout=30)  # FRED stalls on fake browser User-Agents
                r.raise_for_status()
                df = pd.read_csv(StringIO(r.text), index_col=0, parse_dates=True)
                cols[sid] = pd.to_numeric(df.iloc[:, 0], errors='coerce')  # missing days arrive as "."
                break
            except Exception:
                time.sleep(2)
        else:
            failed.append(sid)
    if failed:
        return f"FRED download failed for {', '.join(failed)}" + (" (kept old macro data)" if FRED_PATH.exists() else "")
    pd.DataFrame(cols).sort_index().to_csv(FRED_PATH)
    log(f"Downloaded {len(cols)} macro series from FRED")
    return None


def load_fred():
    return pd.read_csv(FRED_PATH, index_col=0, parse_dates=True) if FRED_PATH.exists() else None


def _fred_features():
    fr = load_fred()
    if fr is None:
        return None
    fr = fr.ffill().shift(1)  # a day's value is published the next day: never let a row see it early
    return pd.DataFrame({'dgs10': fr['DGS10'], 'curve': fr['T10Y2Y'], 'credit': fr['BAA10Y'], 'fed': fr['DFF'],
                         'dgs10_chg20': fr['DGS10'].diff(20), 'credit_chg20': fr['BAA10Y'].diff(20)})


def macro_summary(n=120):
    fr = load_fred()
    if fr is None:
        return None
    out = []
    for sid, label in FRED_SERIES.items():
        s = fr[sid].dropna()
        if len(s) > 21:
            out.append({'id': sid, 'label': label, 'latest': float(s.iloc[-1]), 'change_20': float(s.iloc[-1] - s.iloc[-21]),
                        'as_of': s.index[-1].strftime('%Y-%m-%d'), 'spark': s.tail(n).round(3).tolist()})
    return out


def _market_frame():
    spy = load_prices("SPY")
    return None if spy is None else pd.DataFrame({'mkt_ret_5': spy['Close'].pct_change(5)}).sort_index()


def build_features(df, market):
    """Scale-free features (returns / ratios) so one model can learn across all stocks and price levels."""
    c = df['Close']
    f = pd.DataFrame(index=df.index)
    for n in (1, 5, 10, 20, 60):
        f[f'ret_{n}'] = c.pct_change(n)
    for n in (5, 10, 20):
        f[f'ma_{n}'] = c.rolling(n).mean() / c - 1

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    f['rsi_14'] = (100 - 100 / (1 + gain / loss.replace(0, 1e-6))) / 100

    vol = df['Volume']
    f['vol_chg'] = vol / vol.rolling(20).mean().replace(0, np.nan) - 1
    f['vol_20'] = c.pct_change().rolling(20).std() * 10

    f['rel_5'] = f['ret_5'] - market['mkt_ret_5'].reindex(f.index, method='ffill')
    r = c.pct_change()
    for n in (5, 20, 60):
        f[f'lv{n}'] = np.log(r.rolling(n).std().clip(lower=1e-5))
    f['lvabs'] = np.log(r.abs().clip(lower=1e-4))
    return f[OWN_COLS + VOL_OWN].replace([np.inf, -np.inf], np.nan).astype('float32')


def _pooled_frame(symbols):
    """All stocks stacked (index = date). Has FEATURES, 'target' (next H-day return), 'sym', 'close'."""
    market, fred = _market_frame(), _fred_features()
    if market is None:
        return None
    if fred is None:
        raise RuntimeError("No macro data yet. Click Update Data & Retrain with internet access once.")
    frames = []
    for s in symbols:
        df = load_prices(s)
        if df is None or len(df) < 300:
            continue
        f = build_features(df, market)
        f['target'] = df['Close'].pct_change(H).shift(-H)
        f['close'] = df['Close']
        f['sym'] = s
        frames.append(f)
    if not frames:
        return None
    newest = max(f.index.max() for f in frames)
    D = pd.concat([f for f in frames if (newest - f.index.max()).days <= MAX_STALE_DAYS]).rename_axis('date')
    D = D.join(fred.reindex(D.index.unique(), method='ffill').astype('float32'))
    D['sector'] = D['sym'].map(get_sectors()).fillna('Unknown')
    D['sec_rel_5'] = (D['ret_5'] - D.groupby(['date', 'sector'])['ret_5'].transform('mean')).astype('float32')
    D['rank_ret_20'] = (D.groupby('date')['ret_20'].rank(pct=True) - 0.5).astype('float32')
    D['mkt_lv20'] = D.groupby('date')['lv20'].transform('mean').astype('float32')
    D['rel_lv'] = (D['lv20'] - D['mkt_lv20']).astype('float32')
    return D


def make_model():
    return make_pipeline(StandardScaler(), Ridge(alpha=100.0))


def make_vol_model():
    return make_pipeline(StandardScaler(), Ridge(alpha=1.0))


def _r2(y, q):
    return float(1 - ((y - q) ** 2).sum() / ((y - y.mean()) ** 2).sum())


def _vol_skill(tr, te):
    """Risk forecast: log of the realised daily volatility over the next H days. Scored on unseen data against the
    obvious alternative, 'next week will look like the last 20 days' (re-fitted so it isn't penalised for a constant offset)."""
    trv, tev = tr[tr.fv > 0].dropna(subset=VOL_FEATS), te[te.fv > 0].dropna(subset=VOL_FEATS)
    ytr, yte = np.log(trv.fv.values), np.log(tev.fv.values)
    p = make_vol_model().fit(trv[VOL_FEATS], ytr).predict(tev[VOL_FEATS])
    lv_tr, lv_te = trv.lv20.values, tev.lv20.values
    pb = np.polyval(np.polyfit(lv_tr, ytr, 1), lv_te)
    third = pd.qcut(p, 3, labels=False)
    moves = [float(tev.fv.values[third == k].mean()) for k in range(3)]
    r2, r2b = _r2(yte, p), _r2(yte, pb)
    return {'r2': r2, 'r2_baseline': r2b, 'corr': float(np.corrcoef(yte, p)[0, 1]), 'corr_baseline': float(np.corrcoef(yte, pb)[0, 1]),
            'calm': moves[0], 'normal': moves[1], 'wild': moves[2], 'scale': float(tev.fv.mean() / np.exp(p).mean()),
            'rows': len(tev), 'ok': r2 > r2b + 0.02 and moves[2] > 1.3 * moves[0], 'gain': r2 - r2b}


def _weekly_skill(te):
    """Stock-picking skill on non-overlapping weeks (one cross-section every H trading days), so samples are independent.
    te needs columns 'p' (prediction) and 'target' (realised H-day return) indexed by date."""
    spread, beat, ic = [], [], []
    for d in te.index.unique().sort_values()[::H]:
        g = te.loc[d]
        if len(g) < 50:
            continue
        top, bot = g[g.p >= g.p.quantile(.8)], g[g.p <= g.p.quantile(.2)]
        spread.append(top.target.mean() - bot.target.mean())
        beat.append((top.target > g.target.median()).mean())
        ic.append(g.p.corr(g.target, method='spearman'))
    spread = np.array(spread)
    return {'top_minus_bottom': float(spread.mean()), 'beat_median': float(np.mean(beat)), 'ic': float(np.mean(ic)),
            't_stat': float(spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))), 'weeks': len(spread)}


def train(symbols, log=print):
    """Fit one pooled model on all stocks. Scores it on the last 2 years it never saw, then refits on everything."""
    D = _pooled_frame(symbols)
    if D is None:
        raise RuntimeError("No price data yet (need SPY and stocks). Run Update with internet once.")
    D['fv'] = D.groupby('sym')['ret_1'].transform(lambda s: s.rolling(H).std().shift(-H))  # realised daily vol over t+1..t+H
    D = D.dropna(subset=FEATURES + ['target'])
    cut = D.index.max() - pd.DateOffset(years=2)
    tr, te = D[D.index < cut - pd.Timedelta(days=2 * H)], D[D.index >= cut]  # gap so targets don't overlap
    if len(tr) < 5000 or len(te) < 500:
        raise RuntimeError("Not enough history to train and validate.")
    log(f"Training on {len(tr):,} rows from {D['sym'].nunique()} stocks, validating on {len(te):,}...")

    p = make_model().fit(tr[FEATURES], tr['target']).predict(te[FEATURES])
    t = te['target'].values
    wk = _weekly_skill(te.assign(p=p))
    metrics = {
        'hit': float(((p > 0) == (t > 0)).mean()),
        'always_up_hit': float(max((t > 0).mean(), (t <= 0).mean())),
        **wk,
        'test_rows': len(te),
        'test_from': te.index.min().strftime('%Y-%m-%d'),
        'stocks': int(D['sym'].nunique()),
    }
    # An edge means the model ranks stocks better than chance: weekly top-fifth minus bottom-fifth must be positive and
    # statistically clear (t > 2). Pooled correlation and daily overlapping windows overstate skill, so they don't count.
    metrics['edge'] = metrics['top_minus_bottom'] > 0 and metrics['t_stat'] > 2

    log("Scoring the risk forecast...")
    vol_metrics = _vol_skill(tr, te)
    Dv = D[D.fv > 0].dropna(subset=VOL_FEATS)
    vol_model = make_vol_model().fit(Dv[VOL_FEATS], np.log(Dv.fv.values))
    joblib.dump({'model': make_model().fit(D[FEATURES], D['target']), 'metrics': metrics,
                 'vol_model': vol_model, 'vol_metrics': vol_metrics,
                 'features': FEATURES, 'sklearn': sklearn.__version__, 'trained': date.today().isoformat(), 'horizon': H},
                MODEL_PATH)
    _model_cache.clear()
    _pred_cache.clear()
    log("Training complete.")
    return metrics


def load_model():
    if not MODEL_PATH.exists():
        return None
    mtime = MODEL_PATH.stat().st_mtime
    if _model_cache.get('mtime') != mtime:
        obj = joblib.load(MODEL_PATH)
        if obj.get('sklearn') != sklearn.__version__ or obj.get('features') != FEATURES:
            return None  # saved with another scikit-learn or feature set; retrain
        _model_cache.update(mtime=mtime, obj=obj)
    return _model_cache['obj']


def _pred_key():
    def m(p):
        return p.stat().st_mtime_ns if p.exists() else 0
    return [m(MODEL_PATH), m(_csv("SPY")), m(SHARES_PATH), m(FRED_PATH), m(LOGO_DIR)]


def predict_all(tickers):
    """One row per stock, ranked by predicted H-day return. Returns (rows, error).
    The result is saved to data/predictions.json, so a restart only has to read a file."""
    obj = load_model()
    if obj is None:
        return [], "No trained model (or it is from another scikit-learn version). Click Update Data & Retrain."
    key = _pred_key()
    if _pred_cache.get('key') == key:
        return _pred_cache['out']
    if PRED_PATH.exists():
        saved = json.loads(PRED_PATH.read_text())
        if saved['key'] == key:
            _pred_cache.update(key=key, out=(saved['rows'], None))
            return saved['rows'], None
    names = dict(tickers)
    try:
        D = _pooled_frame(list(names))
    except RuntimeError as e:
        return [], str(e)
    if D is None:
        return [], "No price data. Click Update Data & Retrain."
    closes = D.groupby('sym')['close']
    spark = closes.apply(lambda c: c.tail(40).round(2).tolist())
    year = closes.apply(lambda c: (float(c.tail(252).max()), float(c.tail(252).min())))
    shares = load_shares()
    D = D.dropna(subset=FEATURES)
    latest = D.groupby('sym').tail(1)
    r = obj['model'].predict(latest[FEATURES])
    vf = latest[VOL_FEATS].fillna(latest[VOL_FEATS].median())
    vol = np.exp(obj['vol_model'].predict(vf)) * obj['vol_metrics']['scale'] if 'vol_model' in obj else np.full(len(latest), np.nan)
    third = pd.qcut(vol, 3, labels=['Calm', 'Normal', 'Wild']) if 'vol_model' in obj else [None] * len(latest)
    rows = [{'symbol': row.sym, 'name': names[row.sym], 'sector': row.sector, 'price': float(row.close),
             'pred_return': float(pred), 'pred_price': float(row.close) * (1 + float(pred)),
             'as_of': ts.strftime('%Y-%m-%d'), 'spark': spark[row.sym],
             'market_cap': shares[row.sym] * float(row.close) if row.sym in shares else None,
             'hi52': year[row.sym][0], 'lo52': year[row.sym][1], 'logo': bool(logo_file(row.sym)),
             'vol': None if np.isnan(v) else float(v), 'swing': None if np.isnan(v) else float(v * np.sqrt(H)),
             'risk': None if g is None else str(g)}
            for ts, row, pred, v, g in zip(latest.index, latest.itertuples(), r, vol, third)]
    rows.sort(key=lambda x: x['pred_return'], reverse=True)
    for i, row in enumerate(rows, 1):
        row['rank'] = i
    PRED_PATH.write_text(json.dumps({'key': key, 'rows': rows}))
    _pred_cache.update(key=key, out=(rows, None))
    return rows, None


def history(symbol, n=150):
    df = load_prices(symbol)
    if df is None:
        return None
    df = df.tail(n)
    return {'t': [d.strftime('%Y-%m-%d') for d in df.index], 'o': df['Open'].round(2).tolist(),
            'h': df['High'].round(2).tolist(), 'l': df['Low'].round(2).tolist(),
            'c': df['Close'].round(2).tolist(), 'v': df['Volume'].astype(float).tolist()}


def model_info():
    obj = load_model()
    return None if obj is None else {'metrics': obj['metrics'], 'trained': obj['trained'], 'horizon': obj['horizon'], 'vol': obj.get('vol_metrics')}
