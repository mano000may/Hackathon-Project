"""Download fresh prices and macro data, retrain both models, rebuild the saved predictions.
Same steps as the Update button; used by the scheduled GitHub workflow. Run: python refresh.py"""
import engine

tickers = engine.get_tickers()
symbols = [s for s, _ in tickers]
for step in (engine.refresh_prices, engine.refresh_shares, engine.refresh_logos):
    problem = step(symbols, print)
    if problem:
        print(f"Note: {problem}")
problem = engine.refresh_fred(print)
if problem:
    print(f"Note: {problem}")
engine.train(symbols, print)
engine.predict_all(tickers)
print("Refresh done.")
