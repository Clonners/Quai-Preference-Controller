#!/usr/bin/env python3
import asyncio, json, time, sys, math, logging
import aiohttp, websockets

# ————— CONFIG —————
RPC_HTTP            = "http://127.0.0.1:9001"
RPC_WS              = "ws://127.0.0.1:8001"
BLOCK_TIME_SEC      = 1.0                             # segundos por bloque
BLOCKS_PER_DAY      = int(86400 / BLOCK_TIME_SEC)     # bloques por día
EMA_WINDOW          = 4000                            # bloques para EMA difficulty
ALPHA_DIFF          = 2 / (EMA_WINDOW + 1)            # coef de EMA
ALPHA_RATE          = 0.001                           # para k_Quai update
BASE_K_QI           = 1 / (8 * 10**9)                 # k_Qi base
DOUBLING_PERIOD     = int(365 * BLOCKS_PER_DAY * 2.69) # bloques entre “dobles” de k_Qi
MIN_PREF_CHANGE     = 0.01                            # umbral 1%
INITIAL_BACKOFF     = 1
MAX_BACKOFF         = 60
# ——————————————————

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

async def rpc_call(session, method, params):
    """Llama JSON-RPC y devuelve el campo 'result'."""
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    async with session.post(RPC_HTTP, json=payload) as r:
        return (await r.json()).get("result")

async def process_block(hdr, session, state):
    """
    Calcula reward_Qi, reward_Quai, k_Qi, k_Quai y ajusta miner preference.
    state = {"diff_ema", "k_quai", "last_pref"}
    """
    # 1) Extraer número de bloque y dificultad real del bloque,
    #    soportando tanto el wrapper 'woBody/woHeader' como el formato estándar:
    if "woBody" in hdr and "woHeader" in hdr:
        blk_hex  = hdr["woHeader"]["number"]
        diff_hex = hdr["woBody"]["header"]["difficulty"]  # real, no EMA
    else:
        blk_hex  = hdr.get("number")
        diff_hex = hdr.get("difficulty")

    try:
        blk  = int(blk_hex, 16)
        diff = int(diff_hex, 16)
    except Exception:
        log.warning("No pude parsear bloque/dificultad: %s", hdr)
        return

    # 2) EMA de difficulty (d_star)
    if state["diff_ema"] is None:
        state["diff_ema"] = diff
    else:
        state["diff_ema"] += ALPHA_DIFF * (diff - state["diff_ema"])
    d_star = state["diff_ema"]

    # 3) Actualiza k_Quai según EMA de log2(blockDifficulty)
    state["k_quai"] += ALPHA_RATE * ((math.log2(d_star) if d_star > 0 else 0) - state["k_quai"])

    # 4) Calcular k_Qi con esquema de “doubling”
    doublings = blk // DOUBLING_PERIOD
    k_qi      = BASE_K_QI * (2 ** doublings)

    # 5) Reward Qi por bloque (tokens) → Wei
    qi_wei = int(k_qi * diff * 10**18)

    # 6) Reward Quai por bloque (tokens) → Wei
    quai_wei = int(state["k_quai"] * math.log2(diff) * 10**18)

    # 7) Precio on-chain Qi→Quai: Wei de Quai por Wei de Qi
    price_hex = await rpc_call(session, "quai_qiToQuai", ["0xde0b6b3a7640000", "latest"])
    price_wei = int(price_hex, 16) if price_hex else 0

    # 8) Convierte recompensa Qi a Wei de Quai
    qi_to_quai_wei = qi_wei * price_wei // 10**18

    # 9) Fracción Qi/(Qi + Quai)
    total = qi_to_quai_wei + quai_wei
    pref  = (qi_to_quai_wei / total) if total > 0 else 0.0

    # 10) Ajustar preferencia si cambia más de MIN_PREF_CHANGE
    if state["last_pref"] is None or abs(pref - state["last_pref"]) > MIN_PREF_CHANGE:
        await rpc_call(session, "setMinerPreference", [pref])
        log.info(
            f"[Blk {hex(blk)}] diff={diff:d}  d*={int(d_star)}  "
            f"k_Qi={k_qi:.4g}  k_Quai={state['k_quai']:.4f}  "
            f"QiWei={qi_wei:,}  QuaiWei={quai_wei:,}  "
            f"priceWei={price_wei:,}  → pref={pref:.4f}"
        )
        state["last_pref"] = pref

async def run_controller():
    """Mantiene la suscripción WS y el bucle de procesamiento."""
    state = {"diff_ema": None, "k_quai": 1.0, "last_pref": None}
    backoff = INITIAL_BACKOFF

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info(f"Conectando WS → {RPC_WS}")
                async with websockets.connect(RPC_WS) as ws:
                    log.info("✔ Suscrito a newHeads")
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
                        # Procesar si formato wrapper o estándar
                        if ("woBody" in hdr and "woHeader" in hdr) \
                           or ("number" in hdr and "difficulty" in hdr):
                            await process_block(hdr, session, state)

            except Exception as e:
                log.warning(f"WS error: {e}; reintentando en {backoff}s…")
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

