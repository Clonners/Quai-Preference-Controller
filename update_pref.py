
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quai-pref")

async def rpc_call(session, method, params):
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    }
    async with session.post(RPC_HTTP, json=payload) as r:
        result = await r.json()
        return result.get("result")

async def process_block(hdr, session, state):
    """
    Calcula reward_Qi, reward_Quai, k_Qi, k_Quai y ajusta miner preference.
    state = {"diff_ema", "k_quai", "last_pref"}
    """
    # 1) Extraer número de bloque y dificultad real del bloque
    blk_hex  = hdr.get("number")
    diff_hex = hdr.get("difficulty")
    try:
        blk  = int(blk_hex, 16)
        diff = int(diff_hex, 16)
    except Exception:
        log.warning("No pude parsear number/difficulty: %s", hdr)
        return

    # 2) EMA de difficulty
    if state["diff_ema"] is None:
        state["diff_ema"] = diff
    else:
        state["diff_ema"] += ALPHA_DIFF * (diff - state["diff_ema"])
    d_star = state["diff_ema"]

    # 3) Actualizar k_Quai según EMA de block reward
    k_quai = state["k_quai"] or 1.0
    target_quai = math.log2(d_star) if d_star > 0 else 0
    k_quai += ALPHA_RATE * (target_quai - k_quai)
    state["k_quai"] = k_quai

    # 4) Calcular k_Qi con esquema de “doubling”
    doublings = blk // DOUBLING_PERIOD
    k_qi = BASE_K_QI * (2 ** doublings)

    # 5) Reward Qi por bloque (tokens) → Wei
    qi_wei = int(k_qi * diff * 10**18)

    # 6) Reward Quai por bloque (tokens) → Wei
    quai_wei = int(k_quai * math.log2(diff) * 10**18)

    # 7) Precio on-chain Qi→Quai: Wei de Quai por Wei de Qi
    price_hex = await rpc_call(session, "quai_qiToQuai", ["0xde0b6b3a7640000", "latest"])
    price_wei = int(price_hex, 16) if price_hex else 0

    # 8) Convierte recompensa Qi a Wei de Quai
    qi_to_quai_wei = qi_wei * price_wei // 10**18

    # 9) Fracción óptima Qi/(Qi+Quai)
    total = qi_to_quai_wei + quai_wei
    pref = (qi_to_quai_wei / total) if total > 0 else 0.0

    # 10) Ajustar preferencia si cambia más de MIN_PREF_CHANGE
    if state["last_pref"] is None or abs(pref - state["last_pref"]) > MIN_PREF_CHANGE:
        await rpc_call(session, "setMinerPreference", [pref])
        log.info(
            f"[Blk {blk}] difficulty={diff}  d*={int(d_star)}  "
            f"k_Qi={k_qi:.4g}  k_Quai={k_quai:.4f}  "
            f"QiWei={qi_wei:,}  QuaiWei={quai_wei:,}  "
            f"priceWei={price_wei:,}  → pref={pref:.4f}"
        )
        state["last_pref"] = pref

async def run_controller():
    state = {"diff_ema": None, "k_quai": None, "last_pref": None}
    backoff = INITIAL_BACKOFF

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with websockets.connect(RPC_WS) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "eth_subscribe",
                        "params": ["newHeads"],
                        "id": 1
                    }))
                    await ws.recv()  # ack
                    backoff = INITIAL_BACKOFF

                    async for raw in ws:
                        msg = json.loads(raw)
                        hdr = msg.get("params", {}).get("result", {})
                        if "number" in hdr and "difficulty" in hdr:
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
