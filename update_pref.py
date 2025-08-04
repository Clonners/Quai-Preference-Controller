#!/usr/bin/env python3
import asyncio
import json
import logging

import aiohttp
import numpy as np
import websockets

# ————— CONFIG HISTÓRICO —————
RPC_HTTP_ZONE   = "http://127.0.0.1:9200"     # RPC ZONA para exchangeRate
HIST_BLOCKS     = 600_000                     # bloques atrás a muestrear
SAMPLE_SIZE     = 10_000                      # cuántas muestras tomar
# ————— CONFIG CONTROLADOR —————
RPC_WS          = "ws://127.0.0.1:8001"       # WS para newHeads
RPC_HTTP_EVM    = "http://127.0.0.1:9001"     # JSON-RPC EVM para miner_setMinerPreference
DELTA           = 0.01                        # dead-band ±1 %
INITIAL_BACKOFF = 1
MAX_BACKOFF     = 60
# ————————————————————————————

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("quai-controller")

async def rpc_call(session, url, method, params):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    async with session.post(url, json=payload) as resp:
        data = await resp.json()
        return data.get("result")

async def get_latest_block_number(session):
    res = await rpc_call(session, RPC_HTTP_ZONE, "quai_getBlockByNumber", ["latest", False])
    hdr = res.get("header", {})
    bn = hdr.get("number")
    if isinstance(bn, list):
        bn = bn[0]
    return int(bn, 16)

async def fetch_historical_rates(session):
    latest = await get_latest_block_number(session)
    step   = max(1, HIST_BLOCKS // SAMPLE_SIZE)
    blocks = range(latest - HIST_BLOCKS + 1, latest + 1, step)
    rates = []
    for b in blocks:
        res = await rpc_call(session, RPC_HTTP_ZONE, "quai_getBlockByNumber", [hex(b), False])
        hdr = res.get("header", {})
        er = int(hdr.get("exchangeRate", "0x0"), 16)
        rates.append(er / 1e18)
    return np.array(rates)

def compute_dominant_period(rates):
    centered = rates - rates.mean()
    freqs    = np.fft.rfftfreq(len(rates), d=1)
    fft_vals = np.fft.rfft(centered)
    power    = np.abs(fft_vals)**2
    idx      = np.argmax(power[1:]) + 1
    return 1 / freqs[idx]

# estado global
state = {"rate_ema": None, "last_pref": None}
ALPHA_RATE_EMA = None  # se define tras detección de periodo

async def process_block(hdr, session):
    blk = int(hdr["woHeader"]["number"], 16)
    # 1) leer exchangeRate
    zone = await rpc_call(session, RPC_HTTP_ZONE, "quai_getBlockByNumber", ["latest", False])
    base = int(zone["header"].get("exchangeRate", "0x0"), 16)

    # 2) actualizar EMA
    if state["rate_ema"] is None:
        state["rate_ema"] = base
    else:
        state["rate_ema"] += ALPHA_RATE_EMA * (base - state["rate_ema"])

    lower = state["rate_ema"] * (1 - DELTA)
    upper = state["rate_ema"] * (1 + DELTA)

    last = state["last_pref"]
    if base < lower:
        pref = 1.0   # Qi barato → mina Qi
    elif base > upper:
        pref = 0.0   # Qi caro   → mina Quai
    else:
        pref = last if last is not None else 0.5

    if last is None or abs(pref - last) > 1e-4:
        await rpc_call(session, RPC_HTTP_EVM, "miner_setMinerPreference", [pref])
        state["last_pref"] = pref
        log.info(f"[Blk {blk}] rate={base} EMA={int(state['rate_ema'])} pref={pref:.3f}")

async def run_controller():
    global ALPHA_RATE_EMA
    # — 1) Detección del período dominante —
    async with aiohttp.ClientSession() as sess:
        log.info("Recolectando históricos para FFT…")
        rates  = await fetch_historical_rates(sess)
        period = compute_dominant_period(rates)
        ALPHA_RATE_EMA = 2 / (period + 1)
        log.info(f"Período dominante ≈ {period:.0f} bloques → α={ALPHA_RATE_EMA:.6e}")

    # — 2) Controlador en tiempo real —
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
                log.warning(f"WS error: {e}; retry en {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff*2, MAX_BACKOFF)

if __name__ == "__main__":
    try:
        asyncio.run(run_controller())
    except KeyboardInterrupt:
        log.info("Interrumpido por usuario, saliendo.")

