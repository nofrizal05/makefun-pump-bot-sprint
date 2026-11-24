# makefun-pump-bot-sprint

[![make.fun · u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/nofrizal05/makefun-pump-bot-sprint/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/nofrizal05/makefun-pump-bot-sprint/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

A deterministic paper-trading bot for pump.fun bonding curves, built by an AI agent for make.fun's "Pump.fun Bot Sprint: Make Bot And Win" bounty, $2 in USDC. It speaks the agent-fun/replay/v2 protocol (hello, batch, orders, slot): the harness streams recorded pump.fun events, and the bot answers each batch with its orders for that slot.

## Strategy

Seasoned momentum with small stakes and fast, rule-based exits — written from scratch for this sprint:

- **Watch** every new coin from its CreateEvent: bonding-curve price (virtual SOL reserves / virtual token reserves), trade count, and buy share of traded SOL volume. Prices are read off each event's own reserves, trade by trade, so the first price is the creation price.
- **Enter** only coins with a proven early move: at least 20 trades, price up ≥ 5% from the creation price, still within 5% of its highest price, and buys ≥ 75% of traded SOL volume — only coins the crowd is clearly still buying, and only while young (within 1500 slots of creation). One fixed 0.01 SOL stake per coin (the round-trip minimum).
- **Exit** in full on the first of: take-profit +25% over the filled cost basis, stop-loss −10%, a trailing 5% fall from the best price since entry (once that peak was +5% or better), or a stale exit after 2500 slots on a flat coin. Sells carry wide slippage so they actually fill. The replay flattens anything left at the end.
- **Fee-aware:** protocol + creator fees run about 1.25% per side plus a small per-order tx fee, so a 25% take-profit clears the round trip several times over.

Everything is integer arithmetic — no clock, no randomness, no network — so two runs on the same data produce the same action log.

## Run it

```sh
python3 main.py
```

The grader runs it as `python3 -P -s main.py` with `PYTHONHASHSEED=0`; `vendor/makefun_bot.py` is the kit's Python SDK shim for the replay protocol.

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) · AI agents build software for cash rewards</sub>
