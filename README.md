# AI Stock Predictor

A free, local stock dashboard for the S&P 500. It downloads daily prices, ranks every stock with a simple model, forecasts how risky each one is, and grades its own predictions against a plain baseline. It runs entirely on your computer: no paid APIs, no account, no API key, and the server only listens on `127.0.0.1`.

> **Experimental. Not financial advice.** The return forecast has no reliable edge over simply guessing "up", and the app says so on screen. The risk forecast does beat its baseline. See [Results](#results).

## Features

- **Markets** – the S&P 500 watchlist sorted by market cap, with company logos, sparklines, a 5-day return estimate and a Calm / Normal / Wild risk rating. Filter by sector, search, sort, or switch between table and card layouts.
- **Stock page** – candlestick / line chart with the predicted price and a typical-swing range, 52-week range and key figures.
- **Macro** – interest rates, yields and spreads from FRED.
- **Model report** – hit rate, weekly top-versus-bottom spread, rank correlation and risk-forecast accuracy, all measured on two years of data the model never saw.
- **Landing page** with a real-progress loader and a short tour of how it works.

## Run it

Requires Python 3.11+.

```bash
pip install -r requirements.txt
python server.py
```

The browser opens at <http://127.0.0.1:8000>. On first run choose **Update Data & Retrain** in the sidebar. It downloads about ten years of prices, share counts, logos and FRED series, trains both models and saves them under `data/` and `model/` (both git-ignored). The first update takes a few minutes; later starts take a few seconds.

Run the sanity tests (feature columns, RSI bounds, no look-ahead):

```bash
python test_model.py
```

## How it works

| Step | What happens |
| --- | --- |
| Data | Prices from Yahoo Finance via `yfinance`, the S&P 500 list from Wikipedia, macro series from FRED's public CSV endpoint. |
| Features | 20 numbers per stock per day: momentum, trend, volatility, strength versus peers, and the rate backdrop. |
| Return model | One pooled Ridge regression for all stocks, predicting the 5-day return. |
| Risk model | A HAR-style Ridge regression on log volatility, predicting next-week realized volatility. |
| Scoring | Time-ordered two-year holdout, weekly non-overlapping top-fifth minus bottom-fifth spread with a t-statistic. A model only counts as having an edge if the spread is positive and t > 2. |

## Results

Measured on the held-out period of the model that ships with a fresh training run (your numbers will vary with the data you download):

- **Return forecast:** hit rate about the same as always guessing up, weekly spread not statistically different from zero. Reported as "No Reliable Signal".
- **Risk forecast:** explains more next-week volatility than the "same as lately" baseline, and the Calm / Normal / Wild groups separate clearly in realized daily moves.

## Project layout

```
server.py        FastAPI app and update job (run this)
engine.py        data download, features, models, scoring, predictions
test_model.py    feature and look-ahead checks
static/          web interface (index.html, app.css, landing.css/js, fonts, tech logos)
docs/            presentation (.pptx) and brochure (.pdf)
data/, model/    generated at runtime, not committed
```

## Technology

Python, FastAPI, Uvicorn, pandas, NumPy, scikit-learn, joblib, yfinance, requests, lxml, FRED. The front end is plain JavaScript with canvas and SVG charts, using Inter and JetBrains Mono fonts bundled locally. Logos in `static/tech/` come from [Simple Icons](https://simpleicons.org) (CC0) plus a few drawn for this project.

## Data and attribution

Prices are daily closes from Yahoo Finance, macro data is from FRED (St. Louis Fed), company logos are public favicons cached locally. Check each provider's terms before using the data beyond personal use.

## License

No license file yet. Add one (for example MIT) before publishing if you want others to be able to reuse the code.
