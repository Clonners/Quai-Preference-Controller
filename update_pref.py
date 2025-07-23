#!/usr/bin/env python3
import asyncio, json, time, sys, math, logging
import aiohttp, websockets

# ————— CONFIG —————
RPC_HTTP            = "http://127.0.0.1:9001"
RPC_WS              = "ws://127.0.0.1:8001"
BLOCK_TIME_SEC      = 1.0                             # segundos por bloque
BLOCKS_PER_DAY      = int(86400 / BLOCK_TIME_SEC)     # bloques por día
EMA_WINDOW          = 4000                            # bloques para EMA difficulty
ALPHA_DIFF          = 2 / (EMA_WINDOW + 1)            # coef de EMA :contentReference[oaicite:0]{index=0}
ALPHA_RATE          = 0.001                           # para k_Quai update :contentReference[oaicite:1]{index=1}
BASE_K_QI           = 1 / (8 * 10**9)                 # k_Qi base :contentReference[oaicite:2]{index=2}
DOUBLING_PERIOD     = int(365 * BLOCKS_PER_DAY * 2.69) # bloques entre “dobles” de k_Qi :contentReference[oaicite:3]{index=3}
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
    """Llama JSON-RPC y devuelve data['result'].""" 
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    async with session.post(RPC_HTTP, json=payload) as r:
        return (await r.json()).get("result")

async def process_block(hdr, session, state):
    """
    Calcula reward_Qi, reward_Quai, k_Qi, k_Quai, y ajusta setMinerPreference.
    state = {diff_ema, k_quai, last_pref}
    """
    # 1) Extraer bloque y dificultad
    blk   = hdr["woHeader"]["number"]
    diff  = int(hdr["woBody"]["header"]["minerDifficulty"], 16)

    # 2) EMA de difficulty (targetDifficulty) :contentReference[oaicite:4]{index=4}
    if state["diff_ema"] is None:
        state["diff_ema"] = diff
    else:
        state["diff_ema"] += ALPHA_DIFF * (diff - state["diff_ema"])
    d_star = state["diff_ema"]

    # 3) Normalized difficulty d = diff / d*
    d_norm = diff / d_star if d_star>0 else 1.0

    # 4) Actualiza k_Quai: k_i = k_{i-1} + α (d_norm - 1) :contentReference[oaicite:5]{index=5}
    state["k_quai"] += ALPHA_RATE * (d_norm - 1)

    # 5) Calcula k_Qi dinámico:
    #    baseKqi = 1/(8e9), doblo cada DOUBLING_PERIOD bloques :contentReference[oaicite:6]{index=6}
    blk_num = int(blk, 16)
    doublings = blk_num // DOUBLING_PERIOD
    k_qi = BASE_K_QI * (2 ** doublings)

    # 6) Reward Qi por bloque (tokens), luego a Wei  
    #    reward_Qi = k_qi × diff  
    #    Qi_wei = reward_Qi × 1e18 :contentReference[oaicite:7]{index=7}
    qi_wei = int(k_qi * diff * 10**18)

    # 7) Reward Quai por bloque: k_Quai × log2(diff) tokens → Wei  
    quai_wei = int(state["k_quai"] * math.log2(diff) * 10**18)

    # 8) Precio on-chain Qi→Quai: Wei de Quai por Wei de Qi :contentReference[oaicite:8]{index=8}
    price_hex = await rpc_call(
        session, "quai_qiToQuai",
        ["0xde0b6b3a7640000", "latest"]
    )
    price_wei = int(price_hex, 16) if price_hex else 0

    # 9) Convierte recompensa Qi a Wei de Quai
    qi_to_quai_wei = qi_wei * price_wei // 10**18

    # 10) Fracción óptima Qi/(Qi+Quai)
    total = qi_to_quai_wei + quai_wei
    pref  = (qi_to_quai_wei / total) if total>0 else 0.0

    # 11) Aplicar solo si varió > umbral
    if state["last_pref"] is None or abs(pref - state["last_pref"]) > MIN_PREF_CHANGE:
        await rpc_call(session, "setMinerPreference", [pref])
        log.info(
            f"[Blk {blk}] diff={diff:d}  d*={int(d_star)}  d_norm={d_norm:.4f}  "
            f"k_Qi={k_qi:.4g}  k_Quai={state['k_quai']:.4f}  "
            f"QiWei={qi_wei:,}  QuaiWei={quai_wei:,}  "
            f"priceWei={price_wei:,}  → pref={pref:.4f}"
        )
        state["last_pref"] = pref

async def run_controller():
    """Mantiene la suscripción WS y el bucle de procesamiento."""
    state = { "diff_ema": None, "k_quai": 1.0, "last_pref": None }
    backoff = INITIAL_BACKOFF

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info(f"Conectando WS → {RPC_WS}")
                async with websockets.connect(RPC_WS) as ws:
                    log.info("✔ Suscrito a newHeads")
                    await ws.send(json.dumps({
                        "jsonrpc":"2.0","method":"eth_subscribe",
                        "params":["newHeads"],"id":1
                    }))
                    await ws.recv()  # ack
                    backoff = INITIAL_BACKOFF

                    async for raw in ws:
                        msg = json.loads(raw)
                        hdr = msg.get("params",{}).get("result",{})
                        if "woBody" in hdr and "woHeader" in hdr:
                            await process_block(hdr, session, state)

            except Exception as e:
                log.warning(f"WS error: {e}; reintentando en {backoff}s…")
                await asyncio.sleep(backoff)
                backoff = min(backoff*2, MAX_BACKOFF)

def main():
    try:
        asyncio.run(run_controller())
    except KeyboardInterrupt:
        log.info("🛑 Terminando por Ctrl+C.")
        sys.exit(0)

if __name__ == "__main__":
    main()
