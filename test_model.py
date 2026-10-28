# Run: python test_model.py
import numpy as np
import pandas as pd
import engine

idx = pd.bdate_range("2024-01-01", periods=300)
rng = np.random.default_rng(0)
close = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 300)), index=idx)
df = pd.DataFrame({'Close': close, 'Volume': 1_000_000.0})
market = pd.DataFrame({'mkt_ret_5': 0.0}, index=idx)

f = engine.build_features(df, market)
assert list(f.columns) == engine.OWN_COLS + engine.VOL_OWN
assert f.dropna().shape[0] > 200
assert f['rsi_14'].dropna().between(0, 1).all()
# features at day t must not change when future prices change (no look-ahead)
df2 = df.copy(); df2.iloc[-1, 0] *= 2
assert f.iloc[:-1].equals(engine.build_features(df2, market).iloc[:-1])
print("ok")
