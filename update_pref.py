#!/usr/bin/env python3
import asyncio
import json
import sys
import logging

import aiohttp
import websockets

# ————— CONFIG —————
RPC_WS           = "ws://127.0.0.1:8001"    # WS para newHeads
RPC_HTTP_EVM     = "http://127.0.0.1:9001"  # RPC EVM para setMinerPreference
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
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    async with session.post(url, json=payload) as resp:
        data = await resp.json()
        return data.get("result")

async def process_block(hdr, session):
    # 1) Dificultad del bloque
    diff = int(hdr["woBody"]["header"]["minerDifficulty"], 16)

    # 2) Recompensa en Qi (en Wei)
    qi_wei = int(BASE_K_QI * diff * 10**18)

    # 3) Obtiene del bloque zona:
    zone = await rpc_call(session, RPC_HTTP_ZONE,
                          "quai_getBlockByNumber", ["latest", False])
    zhdr            = zone.get("header", {})
    base_rate_wei   = int(zhdr.get("exchangeRate",  "0x0"), 16)
    discount_wei    = int(zhdr.get("kQuaiDiscount", "0x0"), 16)

    # 4) Tasa efectiva Qi→Quai
    effective_rate_wei = base_rate_wei + discount_wei

    # 5) EMA de effective_rate
    if state.get("rate_ema") is None:
        state["rate_ema"] = effective_rate_wei
    else:
        state["rate_ema"] += ALPHA_RATE_EMA * (
            effective_rate_wei - state["rate_ema"]
        )

    # 6) Dead-band ±1 % alrededor de EMA
    lower = state["rate_ema"] * (1 - DELTA)
    upper = state["rate_ema"] * (1 + DELTA)

    # 7) Cálculo de recompensas en Quai (en Wei)
    direct_quai_wei = qi_wei * base_rate_wei      // 10**18
    qi_to_quai_wei  = qi_wei * effective_rate_wei // 10**18

    # 8) Decidir preferencia continua sólo si sale de la dead-band
    last = state.get("last_pref", 0.5)
    if effective_rate_wei < lower or effective_rate_wei > upper:
        total = direct_quai_wei + qi_to_quai_wei
        if total > 0:
            # invertido: minas Qi si direct_quai_wei > qi_to_quai_wei
            pref = direct_quai_wei / total
        else:
            pref = last
    else:
        pref = last

    # 9) Aplica solo si varió más de 0.0001
    if abs(pref - last) > 1e-4:
        await rpc_call(session, RPC_HTTP_EVM,
                       "setMinerPreference", [pref])
        state["last_pref"] = pref
        log.info(
            f"[Blk {int(hdr['woHeader']['number'],16)}] "
            f"effRate={effective_rate_wei:,}wei  "
            f"EMA={int(state['rate_ema']):,}wei  "
            f"pref={pref:.4f}"
        )

async def run_controller():
    global state
    state = {"rate_ema": None, "last_pref": 0.5}
    backoff = INITIAL_BACKOFF

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info(f"Conectando WS → {RPC_WS}")
                async with websockets.connect(RPC_WS) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc":"2.0",
                        "method":"eth_subscribe",
                        "params":["newHeads"],
                        "id":1
                    }))
                    await ws.recv()  # ack
                    backoff = INITIAL_BACKOFF

                    async for raw in ws:
                        msg = json.loads(raw)
                        hdr = msg.get("params", {}).get("result", {})
                        if "woBody" in hdr and "woHeader" in hdr:
                            await process_block(hdr, session)

            except Exception as e:
                log.warning(f"WS error: {e}; retry en {backoff}s…")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

def main():
    try:
        asyncio.run(run_controller())
    except KeyboardInterrupt:
        log.info("🛑 Terminando por Ctrl+C.")
        sys.exit(0)

if __name__ == "__main__":
    main()

