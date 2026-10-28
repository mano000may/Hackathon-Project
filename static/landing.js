"use strict";
/* Preloader + landing page. Loaded before the main script; it uses the main script's helpers (h, spark, usd, ...) only at call time. */
const Landing = (() => {
  const g = id => document.getElementById(id);
  const root = document.documentElement;
  const motionOn = () => root.dataset.motion !== "reduced";
  let booted = false, shown = 0, target = 0, raf = 0, finished = false;
  try { booted = sessionStorage.getItem("booted") === "1"; } catch (e) { /* storage blocked: the preloader just shows each visit */ }

  /* ---------- preloader: the bar follows real startup steps, eased so each step is visible ---------- */
  function tick() {
    raf = 0;
    shown = motionOn() ? Math.min(target, shown + Math.max(0.006, (target - shown) * 0.09)) : target;
    g("pbar").style.transform = `scaleX(${shown})`;
    g("pbarWrap").setAttribute("aria-valuenow", String(Math.round(shown * 100)));
    g("ppct").textContent = `${Math.round(shown * 100)}%`;
    if (shown < target) raf = setTimeout(tick, 16);
    else if (shown >= 1 && !finished) ready();
  }
  function progress(t, msg) {
    target = Math.max(target, t);
    if (msg) g("pmsg").textContent = msg;
    if (!raf) raf = setTimeout(tick, 16);
  }
  function ready() {
    finished = true;
    g("pmsg").textContent = "Ready.";
    g("enterWrap").hidden = false;
    g("enterBtn").focus({ preventScroll: true });  // the one thing to do on this screen
  }
  function bootError(msg, retry) {
    g("perr").hidden = false;
    g("perrMsg").textContent = msg;
    g("pretry").onclick = () => { g("perr").hidden = true; shown = target = 0; progress(0.04, "Retrying…"); retry(); };
  }
  function enter() {
    const pre = g("preloader");
    pre.classList.add("leaving");
    setTimeout(() => {
      booted = true;
      try { sessionStorage.setItem("booted", "1"); } catch (e) { /* see above */ }
      pre.classList.remove("leaving");
      render(true);
    }, motionOn() ? 560 : 0);
  }
  g("enterBtn").addEventListener("click", enter);

  /* ---------- scroll-triggered reveals ---------- */
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.12 });
  document.querySelectorAll("#landing .reveal").forEach(el => io.observe(el));

  /* in-page links scroll without touching the URL, so the router never sees a hash change */
  g("landing").addEventListener("click", e => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    e.preventDefault();
    const t = g(a.getAttribute("href").slice(1));
    t.scrollIntoView({ behavior: motionOn() ? "smooth" : "auto", block: "start" });
    t.focus({ preventScroll: true });
  });

  /* ---------- hero ---------- */
  function buildHero() {
    const top = [...rows].filter(r => r.market_cap).sort((a, b) => b.market_cap - a.market_cap).slice(0, 4);
    g("heroStats").replaceChildren(...[
      [rows.length ? int.format(rows.length) : "500", "stocks ranked"], ["10 yrs", "of daily prices"],
      [macro && macro.length ? String(macro.length) : "4", "macro series"], ["$0", "paid APIs"]]
      .map(([v, k]) => h("li", {}, h("b", { text: v }), h("span", { text: k }))));
    g("pvRows").replaceChildren(...(top.length ? top.map(r => {
      const d = dayChg(r), trend = r.spark[r.spark.length - 1] / r.spark[0] - 1;
      return h("div", { class: "pv-row" }, logoEl(r, 30),
        h("div", { style: "min-width:0" }, h("p", { class: "sym", translate: "no", text: r.symbol }), h("p", { class: "nm", text: r.name })),
        spark(r.spark, `${r.symbol}, last ${r.spark.length} trading days: ${trend >= 0 ? "up" : "down"} ${pct1.format(Math.abs(trend))}`, false, 84, 28),
        h("p", { class: "px" }, usd.format(r.price), h("small", { class: d >= 0 ? "up" : "down", text: `${d >= 0 ? "▲" : "▼"} ${pct.format(d)}` })));
    }) : [h("p", { class: "muted", text: "Prices appear here after the first update." })]));
    const m = model && model.metrics;
    g("pvDot").className = "dot" + (m && m.edge ? " ok" : "");
    g("pvTxt").textContent = m ? (m.edge ? "Weak signal" : "No reliable signal, scored honestly") : "Model not trained yet";
    g("pvAsOf").textContent = dataInfo ? dateMed.format(utc(dataInfo.newest_bar)) : "";
    const v = model && model.vol;
    g("pvRisk").replaceChildren(...(v ? ["The risk forecast explains ", h("span", { class: "mono", text: pct1.format(v.r2) }), " of next-week volatility, against ",
      h("span", { class: "mono", text: pct1.format(v.r2_baseline) }), " for “same as lately”."] : ["Run an update to score the risk forecast."]));
  }

  /* ---------- feature carousel: slow, pausable, and driven by the CSS progress bar ---------- */
  let ci = 0, slides = [], playing = true;
  const SLIDE_MS = 10000;
  function slideDefs() {
    const topPred = rows.slice(0, 4);
    const m = model && model.metrics, v = model && model.vol;
    const big = [...rows].filter(r => r.market_cap).sort((a, b) => b.market_cap - a.market_cap)[0];
    return [
      { k: "Rank", t: "Every S&P 500 stock, ranked each day", d: `The model estimates each company’s return over the next ${horizon()} trading days, then sorts the whole index so you can see who it likes most and least.`,
        vis: h("div", { class: "mini-list" }, ...(topPred.length ? topPred.map(r => h("div", { class: "mini-row" }, logoEl(r, 30),
          h("div", { class: "grow" }, h("span", { translate: "no", text: r.symbol }), h("small", { text: r.name })),
          h("span", { class: "pill" + (r.pred_return >= 0 ? "" : " down"), text: pct.format(r.pred_return) }))) : [h("p", { class: "muted", text: "Run an update to see live rankings." })])) },
      { k: "Macro", t: "Rates and credit conditions in the loop", d: "Treasury yields, the yield curve, credit spreads and the Fed funds rate come straight from FRED and feed the model as context for every stock.",
        vis: h("div", { class: "macro-grid" }, ...(macro && macro.length ? macro.map(s => h("div", { class: "tile" }, h("p", { class: "k", text: s.label }), h("p", { class: "v", text: `${dec2.format(s.latest)}%` }),
          spark(s.spark, `${s.label} trend`, true, 120, 36))) : [h("p", { class: "muted", text: "Macro data appears after the first update." })])) },
      { k: "Honest", t: "Scored against the boring baseline", d: "Every model is tested on two years it never saw, then compared with simply guessing “up” every time. If it can’t clearly beat that, the app tells you so.",
        vis: m ? h("div", {}, barRow("Model direction accuracy", m.hit, "bar-fg"), barRow("Always guessing up", m.always_up_hit, "bar-fg base"),
          h("p", { class: "muted", style: "font-size:14px", text: `Rank skill ${dec3.format(m.ic)} · weekly t = ${dec2.format(m.t_stat)} · ${m.edge ? "weak signal" : "no reliable signal"} · ${int.format(m.weeks)} unseen weeks` })) : h("p", { class: "muted", text: "Train the model to see its score." }) },
      { k: "Risk", t: "A forecast of how wild each stock will be", d: "Returns are close to unpredictable, but the size of moves is not. The risk forecast is scored on unseen data and beats the “same as lately” baseline.",
        vis: v ? h("div", {}, moveRow("Calm-rated stocks", v.calm), moveRow("Normal-rated stocks", v.normal), moveRow("Wild-rated stocks", v.wild),
          h("p", { class: "muted", style: "font-size:14px", text: `Average daily move afterwards. Explains ${pct1.format(v.r2)} of the variation vs ${pct1.format(v.r2_baseline)} for the baseline.` })) : h("p", { class: "muted", text: "Train the model to see its risk score." }) },
      { k: "Private", t: "Free, local and offline-ready", d: "No accounts, no API keys, no subscriptions. Data is downloaded once, cached on this machine and served only to you.",
        vis: h("ul", { class: "ticks" }, ...["Prices and logos cached on your device", "No paid APIs or keys, only free public data", "Server listens on 127.0.0.1 only", "Works offline after the first update"].map(t => h("li", { text: t }))) },
      { k: "Charts", t: "Charts you can drive with the keyboard", d: "Switch ranges from one month to five years, hover for prices, or use the arrow keys. The predicted price is drawn right on the chart.",
        vis: big ? h("div", { class: "big-spark" }, spark(big.spark, `${big.symbol} last ${big.spark.length} trading days`, false, 320, 150),
          h("p", { text: `${big.symbol}, last ${big.spark.length} trading days` })) : h("p", { class: "muted", text: "Charts appear after the first update." }) },
    ];
  }
  function moveRow(label, v) {  // average daily move, bar scaled 0%–3%
    return h("div", { class: "bar-row" }, h("div", { class: "lab" }, h("span", { text: label }), h("b", { text: pct1.format(v) + " / day" })),
      h("div", { class: "bar-bg" }, h("div", { class: "bar-fg", style: `width:100%;transform:scaleX(${Math.min(1, v / 0.03)})` })));
  }
  function barRow(label, v, cls) {
    return h("div", { class: "bar-row" }, h("div", { class: "lab" }, h("span", { text: label }), h("b", { text: pct1.format(v) })),
      h("div", { class: "bar-bg" }, h("div", { class: cls, style: `width:100%;transform:scaleX(${Math.min(1, Math.max(0, (v - 0.4) / 0.2))})` })));  // scale 40%–60%
  }
  function buildCarousel() {
    slides = slideDefs();
    ci = Math.min(ci, slides.length - 1);
    g("cTrack").replaceChildren(...slides.map((s, i) => h("div", { class: "c-slide", role: "group", "aria-roledescription": "slide", "aria-label": `${i + 1} of ${slides.length}: ${s.k}` },
      h("div", {}, h("span", { class: "eyebrow", text: s.k }), h("h3", { text: s.t }), h("p", { class: "muted", text: s.d })), h("div", { class: "c-vis" }, s.vis))));
    g("cDots").replaceChildren(...slides.map((s, i) => h("button", { type: "button", "aria-label": `Go to slide ${i + 1}: ${s.k}`, onclick: () => goTo(i) })));
    goTo(ci);
  }
  function restartProgress() {
    const p = g("cProg");
    p.style.animation = "none"; void p.offsetWidth; p.style.animation = "";  // restart the CSS animation
  }
  function goTo(i) {
    ci = (i + slides.length) % slides.length;
    g("cTrack").style.transform = `translateX(${-ci * 100}%)`;
    [...g("cTrack").children].forEach((s, k) => { s.inert = k !== ci; });
    [...g("cDots").children].forEach((d, k) => d.setAttribute("aria-current", String(k === ci)));
    restartProgress();
  }
  function setPlaying(p) {
    playing = p;
    g("carousel").classList.toggle("paused", !p);
    g("cTrack").setAttribute("aria-live", p ? "off" : "polite");  // announce slide changes only when the viewer drives them
    g("cPlay").setAttribute("aria-label", p ? "Pause Slideshow" : "Play Slideshow");
    g("cPlayIcon").setAttribute("href", p ? "#i-pause" : "#i-play");
  }
  g("cPrev").addEventListener("click", () => goTo(ci - 1));
  g("cNext").addEventListener("click", () => goTo(ci + 1));
  g("cPlay").addEventListener("click", () => setPlaying(!playing));
  g("cProg").addEventListener("animationend", () => goTo(ci + 1));
  g("carousel").addEventListener("keydown", e => {
    if (e.key === "ArrowLeft") { goTo(ci - 1); e.preventDefault(); } else if (e.key === "ArrowRight") { goTo(ci + 1); e.preventDefault(); }
  });
  g("carousel").style.setProperty("--slide-ms", `${SLIDE_MS}ms`);

  /* ---------- how it works: pipeline diagram ---------- */
  const STEPS = [
    { id: "ingest", t: "Data Ingestion", s: "yfinance · FRED · Wikipedia",
      p: "Python downloads ten years of daily prices for the S&P 500 and SPY, four daily macro series from FRED, and the company list with sectors from Wikipedia. Everything is cached as files on disk.",
      b: ["Prices come in batches of 100 symbols with a retry", "Only missing logos are fetched on later updates", "After the first run the app works offline"], out: "Raw prices, macro series, share counts",
      live: () => dataInfo ? `${int.format(dataInfo.stocks)} symbols · latest ${dateMed.format(utc(dataInfo.newest_bar))}` : "waiting for data" },
    { id: "clean", t: "Cleaning & Features", s: "pandas · NumPy",
      p: "Corrupt rows are dropped, stale tickers are excluded, and odd data is flagged (gaps, 40%+ one-day jumps). Then 20 scale-free features are built for every stock on every day.",
      b: ["Returns over 1, 5, 10, 20 and 60 days", "Moving-average gaps, RSI, volume change, volatility", "Strength versus the market, versus sector peers, and rank among all stocks", "Rates, curve, credit spread, Fed funds"], out: "~940,000 stock-days of features",
      live: () => dataInfo ? `${dataInfo.flagged} stocks flagged` : "" },
    { id: "model", t: "Prediction Model", s: "scikit-learn",
      p: "Two small models learn from every stock at once. A Ridge regression estimates the return over the next five trading days, and a volatility model forecasts how large each stock’s moves will be. Both are scored on the last two years they never trained on.",
      b: ["Time-ordered split, so there is no peeking at the future", "Returns are checked week by week against chance and “always guess up”", "Risk is checked against “volatility stays as lately”", "Saved to disk with the versions that built them"], out: "A return estimate, a risk forecast, and honest scores for both",
      live: () => model ? `trained ${dateMed.format(utc(model.trained))}` : "not trained yet" },
    { id: "server", t: "Python Server", s: "FastAPI · Uvicorn",
      p: "A small local Python web server (FastAPI running on Uvicorn, bound to 127.0.0.1) turns the files and the model into JSON. Updating runs as a background job, and finished predictions are saved so restarts are instant.",
      b: ["Listens on this machine only", "Responses are gzip-compressed", "Symbols are validated before any file is touched"], out: "JSON endpoints for the browser",
      endpoints: [["GET", "/api/status"], ["GET", "/api/predictions"], ["GET", "/api/macro"], ["GET", "/api/history/{symbol}"], ["GET", "/logos/{symbol}"], ["POST", "/api/update"]],
      live: () => "127.0.0.1:8000" },
    { id: "signal", t: "Signals & Interface", s: "JavaScript · Canvas",
      p: "The page ranks stocks by predicted return, draws sparklines and charts, and shows the model’s report card next to every prediction. A signal is a ranking, not advice.",
      b: ["Sort and filter 500 stocks instantly", "The Model Report shows when the model has no edge", "Charts and tables are keyboard accessible"], out: "Ranked signals you can inspect",
      live: () => rows.length ? `${int.format(rows.length)} ranked` : "" },
  ];
  let step = "ingest";
  function buildFlow() {
    const parts = [];
    STEPS.forEach((s, i) => {
      parts.push(h("button", { class: "node", type: "button", "aria-pressed": String(s.id === step), onclick: () => { step = s.id; buildFlow(); } },
        h("span", { class: "n", "aria-hidden": "true", text: String(i + 1) }), h("b", { text: s.t }), h("small", { text: s.s }), h("em", { text: s.live() })));
      if (i < STEPS.length - 1) parts.push(h("div", { class: "link", "aria-hidden": "true", style: `--i:${i * 0.45}s` }, h("i")));
    });
    g("flow").replaceChildren(...parts);
    const s = STEPS.find(x => x.id === step), idx = STEPS.indexOf(s) + 1;
    g("flowDetail").replaceChildren(
      h("div", {}, h("span", { class: "eyebrow", text: `Step ${idx} of ${STEPS.length}` }), h("h3", { style: "margin-top:10px", text: s.t }), h("p", { text: s.p }), h("ul", {}, ...s.b.map(x => h("li", { text: x })))),
      h("div", { class: "side-box" }, h("h4", { text: s.endpoints ? "Endpoints" : "Output" }),
        s.endpoints ? h("div", { class: "chips" }, ...s.endpoints.map(([m, p]) => h("span", { class: "chip-e" }, h("b", { text: m }), p))) : h("p", { style: "margin:0;color:var(--text)", text: s.out }),
        s.endpoints && h("p", { style: "margin-top:12px;font-size:13px", text: "Flask would do a similar job; this project uses FastAPI for built-in validation and async serving." })));
  }

  /* ---------- model status cards ---------- */
  function buildStatus() {
    const m = model && model.metrics, v = model && model.vol;
    const realLogos = rows.filter(r => r.logo).length;
    const card = (title, ok, big, text) => h("article", { class: "scard" }, h("header", {}, h("span", { text: title }), h("span", { class: "dot" + (ok ? " ok" : ""), "aria-hidden": "true" })), h("p", { class: "v", text: big }), h("p", { text }));
    g("statusCards").replaceChildren(
      card("Prediction Model", !!(m && m.edge), "Ridge Regression", model ? `20 features · ${model.horizon}-day horizon · trained ${dateMed.format(utc(model.trained))}` : "Not trained yet. Open the dashboard and run an update."),
      card("Accuracy Test", !!(m && m.edge), m ? `${pct1.format(m.hit)} vs ${pct1.format(m.always_up_hit)}` : "–", m ? `Direction accuracy against always guessing up. Rank skill ${dec3.format(m.ic)}. Verdict: ${m.edge ? "weak signal" : "no reliable signal"}.` : "Appears after training."),
      card("Risk Forecast", !!(v && v.ok), v ? `${pct1.format(v.r2)} explained` : "–", v ? `vs ${pct1.format(v.r2_baseline)} for the “same as lately” baseline. Calm-rated stocks move ${pct1.format(v.calm)} a day, wild-rated ${pct1.format(v.wild)}.` : "Appears after training."),
      card("Price Data", !!dataInfo, dataInfo ? `${int.format(dataInfo.stocks)} stocks` : "–", dataInfo ? `Latest prices ${dateMed.format(utc(dataInfo.newest_bar))} · ${dataInfo.flagged} flagged for unusual data.` : "No data downloaded yet."),
      card("Macro & Logos", !!(macro && macro.length), macro && macro.length ? `${macro.length} FRED series` : "–", `${macro && macro.length ? `Latest ${dateMed.format(utc(macro[0].as_of))}. ` : ""}${int.format(realLogos)} real company logos cached, initials for the rest.`));
  }

  /* ---------- tech stack ---------- */
  // [logo file in /static/tech or null, name, what it does here]
  const STACK = [
    ["Backend", [["python", "Python 3", "Language for the data and model code"], ["fastapi", "FastAPI", "Serves the API and this page"], ["uvicorn", "Uvicorn", "ASGI server, bound to 127.0.0.1 only"]]],
    ["Data & models", [["pandas", "pandas", "Prices, features and rankings"], ["numpy", "NumPy", "Fast array maths"], ["scikitlearn", "scikit-learn", "Ridge regression, scaling, pipelines"]]],
    ["Data sources", [["yfinance", "yfinance", "Daily prices from Yahoo Finance"], ["fred", "FRED", "Rates, yields and spreads, no key needed"], ["wikipedia", "Wikipedia", "Current S&P 500 list and sectors"]]],
    ["Interface", [["javascript", "JavaScript", "No framework, no build step"], ["canvas", "Canvas & SVG", "Candlestick charts and sparklines"], [null, "Inter + JetBrains Mono", "Fonts bundled locally"]]]];
  function buildStack() {
    g("stackGrid").replaceChildren(...STACK.map(([title, items]) => h("section", { class: "stack-col" }, h("h3", { text: title }),
      h("ul", {}, ...items.map(([file, name, note]) => h("li", {},
        h("span", { class: "tlogo", "aria-hidden": "true" }, file ? h("img", { src: `/static/tech/${file}.svg`, alt: "", width: 28, height: 28, loading: "lazy", decoding: "async" }) : h("b", { text: "Aa" })),
        h("span", { class: "tname" }, h("strong", { text: name }), h("small", { text: note }))))))));
  }

  return {
    isBooted: () => booted, progress, bootError, ready: () => finished,
    update() { buildHero(); buildCarousel(); buildFlow(); buildStatus(); buildStack(); setPlaying(playing); },
    resetMotion() { restartProgress(); },
  };
})();
