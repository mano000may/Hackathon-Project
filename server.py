"""Local web backend. Run: python server.py  ->  http://127.0.0.1:8000 (listens on this machine only)."""
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import engine

app = FastAPI(title="AI Stock Predictor")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
TICKERS = engine.get_tickers()
SYMBOLS = {s for s, _ in TICKERS}
job = {"running": False, "log": [], "error": None}
job_lock = threading.Lock()


def _log(msg):
    job["log"].append(msg)


def _run_update():
    try:
        _log("Downloading prices...")
        problem = engine.refresh_prices([s for s, _ in TICKERS], _log)
        if problem:
            _log(f"{problem}. Using saved prices where needed.")
        problem = engine.refresh_shares([s for s, _ in TICKERS], _log)
        if problem:
            _log(f"{problem}.")
        problem = engine.refresh_logos([s for s, _ in TICKERS], _log)
        if problem:
            _log(f"{problem}.")
        _log("Downloading macro data from FRED...")
        problem = engine.refresh_fred(_log)
        if problem:
            _log(f"{problem}.")
        engine.train([s for s, _ in TICKERS], _log)
        _log("Computing predictions...")
        engine.predict_all(TICKERS)  # saved to disk, so the next startup is instant
    except Exception as e:
        job["error"] = str(e)
    finally:
        job["running"] = False


@app.get("/")
def index():
    static = Path(__file__).parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "landing.css", "landing.js"):  # version by file time so a browser never serves a stale stylesheet
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((static / name).stat().st_mtime)}")
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    return {"job": job, "model": engine.model_info(), "data": engine.quality_summary()}


@app.post("/api/update")
def update():
    with job_lock:
        if job["running"]:
            raise HTTPException(409, "Update already running")
        job.update(running=True, log=[], error=None)
    threading.Thread(target=_run_update, daemon=True).start()
    return {"started": True}


@app.get("/api/predictions")
def predictions():
    rows, err = engine.predict_all(TICKERS)
    return {"rows": rows, "error": err}


@app.get("/logos/{symbol}")
def logo(symbol: str):
    if symbol not in SYMBOLS:
        raise HTTPException(404, "Unknown symbol")
    cache = {"Cache-Control": "public, max-age=86400"}
    f = engine.logo_file(symbol)
    if f is None:  # every listed stock gets an image, so the page never shows a broken one
        return Response(engine.monogram_svg(symbol), media_type="image/svg+xml", headers=cache)
    return FileResponse(f, headers=cache)


@app.get("/api/macro")
def macro():
    return {"series": engine.macro_summary()}


@app.get("/api/history/{symbol}")
def history(symbol: str, n: int = Query(150, ge=20, le=2600)):
    if symbol not in SYMBOLS:  # also keeps arbitrary file names out of load_prices
        raise HTTPException(404, "Unknown symbol")
    h = engine.history(symbol, n)
    if h is None:
        raise HTTPException(404, "No price data")
    return h


if __name__ == "__main__":
    threading.Thread(target=engine.predict_all, args=(TICKERS,), daemon=True).start()  # warm the cache, first call takes ~10s
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
