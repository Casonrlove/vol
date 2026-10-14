import pandas as pd
from record_chain import pick, implied

calls = pd.DataFrame({"strike": [98., 100., 102.], "bid": [3.0, 2.0, 0.0], "ask": [3.4, 2.4, 0.0],
                      "lastPrice": [3.1, 2.2, 0.5], "impliedVolatility": [.30, .25, .20]})
puts = pd.DataFrame({"strike": [98., 100., 102.], "bid": [1.0, 2.0, 4.0], "ask": [1.2, 2.6, 4.4],
                     "lastPrice": [1.1, 2.3, 4.1], "impliedVolatility": [.35, .27, .22]})

# nearest strike is 100; mids (2.0+2.4)/2 and (2.0+2.6)/2; move = 4.5 / 100.4
k, cm, pm, ci, pi = pick(calls, puts, 100.4)
assert (k, cm, pm, ci, pi) == (100., 2.2, 2.3, .25, .27), (k, cm, pm, ci, pi)
assert abs(implied(cm, pm, 100.4) - 4.5 / 100.4) < 1e-12

# strike 102 has bid 0 on the call side -> call mid falls back to lastPrice 0.5; put mid (4.0+4.4)/2 = 4.2
k, cm, pm, ci, pi = pick(calls, puts, 101.9)
assert (k, cm, pm) == (102., 0.5, 4.2), (k, cm, pm)
print("ok")
