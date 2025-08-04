#!/usr/bin/env python3
import asyncio
import json
import sys
import logging

import aiohttp
import websockets

# ————— CONFIG —————
RPC_WS           = "ws://127.0.0.1:8001"    # WS para newHeads
RPC_HTTP_EVM     = "http://127.0.0.1:9001"  # RPC EVM para miner_setMinerPreference
RPC_HTTP_ZONE    = "http://127.0.0.1:9200"  # RPC Zona para exchangeRate + kQuaiDiscount
BASE_K_QI        = 1 / (8 * 10**9)         # k_Qi constante (lineal)
ALPHA_RATE_EMA   = 0.001                   # paso para EMA de effective_rate
DELTA            = 0.01                    # dead-band ±1 %
INITIAL_BACKOFF  = 1
MAX_BACKOFF      = 60
# ——————————————————

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

async def rpc_call(session, url, method, params):
    """Llama al RPC y devuelve el campo 'result'."""
    log.info(f"RPC CALL → {method} @ {url} params={params}")
    try:
        payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            result = data.get("result")
            log.info(f"RPC RESULT ← {method}: {result}")
            return result
    except Exception as e:
        log.warning(f"RPC error {method}@{url}: {e}")
        return None

async def process_block(hdr, session):
    blk_num = int(hdr["woHeader"]["number"], 16)
    log.info(f"--- Processing block {blk_num} ---")

    # 1) Dificultad del bloque
    diff = int(hdr["woBody"]["header"]["minerDifficulty"], 16)
    log.info(f"Block difficulty: {diff}")

    # 2) Recompensa en Qi (en Wei)
    qi_wei = int(BASE_K_QI * diff * 10**18)
    log.info(f"Qi reward (wei): {qi_wei}")

    # 3) Obtiene del bloque zona:
    log.info("Fetching zone block data…")
    zone = await rpc_call(session, RPC_HTTP_ZONE,
                          "quai_getBlockByNumber", ["latest", False])
    if not zone:
        log.error("No zone data, skipping block")
        return
    zhdr = zone.get("header", {})
    base_rate_wei = int(zhdr.get("exchangeRate", "0x0"), 16)
    discount_wei  = int(zhdr.get("kQuaiDiscount", "0x0"), 16)
    log.info(f"Base exchangeRate (wei): {base_rate_wei}")
    log.info(f"kQuaiDiscount (wei): {discount_wei}")

    # 4) Tasa efectiva Qi→Quai
    effective_rate_wei = base_rate_wei + discount_wei
    log.info(f"Effective rate (wei): {effective_rate_wei}")

    # 5) EMA de effective_rate
    if state.get("rate_ema") is None:
        state["rate_ema"] = effective_rate_wei
        log.info(f"Initialized EMA with {effective_rate_wei}")
    else:
        before_ema = state["rate_ema"]
        state["rate_ema"] += ALPHA_RATE_EMA * (
            effective_rate_wei - state["rate_ema"]
        )
        log.info(f"Updated EMA: {before_ema} → {state['rate_ema']}")

    # 6) Dead-band ±1 % alrededor de EMA
    lower = state["rate_ema"] * (1 - DELTA)
    upper = state["rate_ema"] * (1 + DELTA)
    log.info(f"Dead-band lower={lower}, upper={upper}")

    # 7) Cálculo de recompensas en Quai (en Wei)
    direct_quai_wei = qi_wei * base_rate_wei      // 10**18
    qi_to_quai_wei  = qi_wei * effective_rate_wei // 10**18
    log.info(f"Direct Quai reward (wei): {direct_quai_wei}")
    log.info(f"Qi→Quai reward (wei): {qi_to_quai_wei}")

    # 8) Decidir preferencia continua sólo si sale de la dead-band
    last = state.get("last_pref", 0.5)
    if effective_rate_wei < lower or effective_rate_wei > upper:
        log.info("Effective rate outside dead-band, recalculating pref")
        total = direct_quai_wei + qi_to_quai_wei
        if total > 0:
            # invertido: minas Qi si direct_quai_wei > qi_to_quai_wei
            pref = direct_quai_wei / total
            log.info(f"Calculated pref: {pref}")
        else:
            pref = last
            log.warning("Total reward zero, using last pref")
    else:
        pref = last
        log.info("Effective rate within dead-band, keeping last pref")

    # 9) Aplica solo si varió más de 0.0001
    if abs(pref - last) > 1e-4:
        log.info(f"Pref changed {last} → {pref}, calling miner_setMinerPreference")
        await rpc_call(session, RPC_HTTP_EVM,
                       "miner_setMinerPreference", [pref])
        state["last_pref"] = pref
    else:
        log.info(f"Pref change {last} → {pref} below threshold, skipping")

async def run_controller():
    global state
    state = {"rate_ema": None, "last_pref": 0.5}
    backoff = INITIAL_BACKOFF

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info(f"Connecting to WS → {RPC_WS}")
                async with websockets.connect(RPC_WS) as ws:
                    log.info("WebSocket connected, sending subscribe…")
                    await ws.send(json.dumps({
                        "jsonrpc":"2.0",
                        "method":"eth_subscribe",
                        "params":["newHeads"],
                        "id":1
                    }))
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5)
                        log.info(f"Subscription ACK received: {msg}")
                    except asyncio.TimeoutError:
                        log.error("Timeout waiting for subscription ACK")
                        return

                    backoff = INITIAL_BACKOFF
                    async for raw in ws:
                        log.info(f"New WS message: {raw[:200]}")
                        msg = json.loads(raw)
                        hdr = msg.get("params", {}).get("result", {})
                        if "woBody" in hdr and "woHeader" in hdr:
                            await process_block(hdr, session)

            except Exception as e:
                log.warning(f"WS error: {e}; retry in {backoff}s…")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

def main():
    try:
        asyncio.run(run_controller())
    except KeyboardInterrupt:
        log.info("🛑 Terminating on user interrupt.")
        sys.exit(0)

if __name__ == "__main__":
    main()

