"""Pump.fun paper-trading bot for make.fun's "Pump.fun Bot Sprint: Make Bot And Win".

Protocol: agent-fun/replay/v2. The harness sends a ``hello`` (capital, caps, fees,
latency in slots), then one ``batch`` per slot window, then ``done``. Every batch
carries the slot, the events since the last batch (CreateEvent / TradeEvent /
CompleteEvent), fills for our earlier orders, marks with the latest reserves,
and the account (cash + positions). We answer each batch with exactly one
reply echoing its seq, carrying our orders for that slot.

Strategy (written from scratch for this competition): early momentum with
fast, rule-based exits.

* Watch every new coin from its CreateEvent. Track its bonding-curve price
  (virtual SOL reserves / virtual token reserves, integer-scaled so the
  comparison is exact) from the first price we see, plus its trade count and
  how much of its traded SOL volume was buys.
* Enter only while the coin is still young and rising: at least 8 trades,
  price up >= 5% from the first price seen, price still within 5% of the
  highest price seen so far (still climbing, not already faded), and buys at
  least 65% of traded SOL volume. One fixed stake per coin (0.04 SOL, or the
  harness size cap when smaller, but never below the 0.01 SOL round-trip
  minimum), so no single coin can hurt the book much.
* Exit fast, in full, on the first of: take-profit +25% over the filled cost
  basis, stop-loss -10%, a trailing fall of 8% from the best price since
  entry (once that peak was +10% or better), or a stale exit after 2500 slots
  when the coin never got within 5% of the entry price again. Sells carry
  wide slippage so they actually fill — a bad exit beats holding a dying
  coin. The replay flattens anything left at the end.
* Fee-aware: protocol + creator fees run about 1.25% per side plus a small
  per-order tx fee, so a 10% entry move and a 30% take-profit clear the round
  trip several times over; we never chase a move smaller than the fees.

Determinism: integer arithmetic only, no clock, no randomness, no network,
and every loop follows the batch's event order / first-seen insertion order,
so two runs on the same data produce the same action log.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script directory off sys.path)

LAMPORTS_PER_SOL = makefun_bot.LAMPORTS_PER_SOL
SCALE = 10 ** 9  # price is lamports-per-token scaled by this, kept as an int

STAKE_LAMPORTS = 40_000_000          # 0.04 SOL per coin
MIN_ROUND_TRIP_LAMPORTS = 10_000_000  # 0.01 SOL: the guardrail minimum per round trip
MAX_SLIPPAGE_BPS = 1000              # 10% on buys: keep entries from filling far above the signal
SELL_SLIPPAGE_BPS = 10_000           # exits must fill: a bad exit beats holding a dying coin

MIN_TRADES_TO_ENTER = 8
MAX_AGE_SLOTS_TO_ENTER = 1500       # only very young coins, still in their launch window
ENTRY_RISE_BPS = 500                # price up >= 5% from first price seen: enter the move early
ENTRY_NEAR_PEAK_BPS = 9500          # price within 5% of the highest price seen
ENTRY_MIN_BUY_SHARE_BPS = 6500      # buys >= 65% of traded SOL volume

TAKE_PROFIT_BPS = 12500             # sell at +25% over filled cost basis
STOP_LOSS_BPS = 9000                # sell at -10% under filled cost basis
TRAILING_DROP_BPS = 9200            # sell after an 8% fall from the peak since entry
TRAILING_ACTIVATE_BPS = 11000       # ... once the peak reached +10%
STALE_SLOTS = 2500                  # give up on a flat coin after this many slots
STALE_EXIT_BPS = 10500              # ... unless it is already up >= 5%


def price_scaled(curve):
    """Bonding-curve price in lamports per token, scaled by SCALE (0 if unknown)."""
    if curve is None or curve.virtual_token_reserves <= 0:
        return 0
    return curve.virtual_quote_reserves * SCALE // curve.virtual_token_reserves


class Coin:
    """Per-mint bookkeeping, kept in first-seen order."""

    __slots__ = (
        "first_price", "peak_price", "trades", "buy_sol", "sell_sol",
        "created_slot", "decided", "entry_price", "entry_slot", "peak_since_entry",
    )

    def __init__(self, first_price, slot):
        self.first_price = first_price
        self.peak_price = first_price
        self.trades = 0
        self.buy_sol = 0
        self.sell_sol = 0
        self.created_slot = slot
        self.decided = False
        self.entry_price = 0
        self.entry_slot = 0
        self.peak_since_entry = 0


coins = {}  # mint -> Coin, insertion order = first-seen order


def _observe(state, batch):
    """Fold this batch's events into per-coin stats (batch order, deterministic)."""
    slot = batch["slot"]
    for e in batch["events"]:
        mint = e["mint"]
        kind = e["event"]
        curve = state.curves.get(mint)
        price = price_scaled(curve)
        coin = coins.get(mint)
        if coin is None:
            if price <= 0:
                continue
            coin = Coin(price, slot)
            coins[mint] = coin
        if kind == "TradeEvent":
            coin.trades += 1
            amount = int(e["sol_amount"])
            if e["is_buy"]:
                coin.buy_sol += amount
            else:
                coin.sell_sol += amount
        if price > 0:
            if price > coin.peak_price:
                coin.peak_price = price
            if mint in state.positions and coin.entry_price > 0:
                if price > coin.peak_since_entry:
                    coin.peak_since_entry = price


def _sync_entries(state):
    """Pick up the filled cost basis of positions we hold but have not priced yet."""
    for mint, holding in state.positions.items():
        coin = coins.get(mint)
        if coin is None:
            continue
        if coin.entry_price == 0 and holding.token_amount > 0:
            coin.entry_price = holding.cost_lamports * SCALE // holding.token_amount
            coin.entry_slot = state.slot
            coin.peak_since_entry = coin.entry_price
            coin.decided = True


def on_batch(state, batch):
    _observe(state, batch)
    _sync_entries(state)
    caps = state.hello["caps"]
    tx = state.tx_fee_lamports()
    free = state.free_cash_lamports()
    orders = []
    max_orders = caps["max_orders_per_batch"]

    # --- exits first: free cash and slots before any new entry ---
    for mint in list(state.positions.keys()):
        if len(orders) >= max_orders:
            break
        holding = state.positions.get(mint)
        if holding is None or holding.token_amount <= 0:
            continue
        coin = coins.get(mint)
        curve = state.curves.get(mint)
        if coin is None or curve is None or curve.complete:
            continue
        price = price_scaled(curve)
        if price <= 0 or coin.entry_price <= 0:
            continue
        if price > coin.peak_since_entry:
            coin.peak_since_entry = price
        # Compare price against entry with integer cross-multiplication in bps.
        rel = price * 10_000 // coin.entry_price
        peak_rel = coin.peak_since_entry * 10_000 // coin.entry_price
        should_sell = False
        if rel >= TAKE_PROFIT_BPS:
            should_sell = True  # take-profit: bank the +30%
        elif rel <= STOP_LOSS_BPS:
            should_sell = True  # stop-loss: cut the -15%
        elif peak_rel >= TRAILING_ACTIVATE_BPS and rel * 10_000 // peak_rel <= TRAILING_DROP_BPS:
            should_sell = True  # trailing stop: 8% off a peak that was +10% or better
        elif state.slot - coin.entry_slot >= STALE_SLOTS and rel < STALE_EXIT_BPS:
            should_sell = True  # stale: flat for too long, redeploy the cash
        if should_sell:
            orders.append({
                "side": "sell", "mint": mint,
                "token_amount": holding.token_amount,
                "max_slippage_bps": SELL_SLIPPAGE_BPS,
            })
            free -= tx

    # --- entries: young, rising, buy-dominated coins, one stake each ---
    slots_open = caps["max_open_positions"] - state.committed_position_count()
    for e in batch["events"]:
        if len(orders) >= max_orders or slots_open <= 0:
            break
        mint = e["mint"]
        coin = coins.get(mint)
        if coin is None or coin.decided:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            coin.decided = True
            continue
        if coin.trades < MIN_TRADES_TO_ENTER:
            continue
        if state.slot - coin.created_slot > MAX_AGE_SLOTS_TO_ENTER:
            coin.decided = True
            continue
        price = price_scaled(curve)
        if price <= 0 or coin.first_price <= 0:
            continue
        rise = price * 10_000 // coin.first_price
        if rise < 10_000 + ENTRY_RISE_BPS:
            continue
        if price * 10_000 // coin.peak_price < ENTRY_NEAR_PEAK_BPS:
            continue  # already fading from its high; do not chase the top
        volume = coin.buy_sol + coin.sell_sol
        if volume <= 0:
            continue
        buy_share = coin.buy_sol * 10_000 // volume
        if buy_share < ENTRY_MIN_BUY_SHARE_BPS:
            continue
        cap = state.max_buy_lamports(mint)
        amount = min(cap, STAKE_LAMPORTS)
        if amount < MIN_ROUND_TRIP_LAMPORTS:
            continue
        if free < amount + tx:
            continue
        coin.decided = True
        orders.append({
            "side": "buy", "mint": mint,
            "quote_lamports": amount,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        })
        free -= amount + tx
        slots_open -= 1
    return orders


if __name__ == "__main__":
    makefun_bot.run(on_batch)
