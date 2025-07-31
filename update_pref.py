#!/usr/bin/env python3
import asyncio
import json
import math
import sys
import aiohttp
import websockets

RPC_HTTP = "http://127.0.0.1:9001"
RPC_WS = "ws://127.0.0.1:8001"  # WebSocket para nuevos bloques

# Coeficientes de emisión (Token Dynamics)
K_QI   = 1 / (8 * 10**9)  # Qi: lineal
K_QUAI = 1.0              # Quai: logarítmico

async def rpc_call(session, method, params):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    async with session.post(RPC_HTTP, json=payload) as resp:
        data = await resp.json()
        return data.get("result")

async def process_block(hdr, session, state):
    """
    Calcula reward_Qi, reward_Quai, k_Qi, k_Quai y ajusta miner preference.
    state = {"last_pref"}
    """
    # 1) Extraer número de bloque y dificultad real
    try:
        blk  = int(hdr["number"], 16)
        diff = int(hdr["difficulty"], 16)
    except KeyError:
        print(f"Error al procesar el bloque: {hdr}")
        return

    # 2) Calcular recompensa por bloque
    qi_reward   = K_QI   * diff
    quai_reward = K_QUAI * math.log2(diff)

    # 3) Consultar ratio on-chain Qi → Quai para este bloque
    one_qi_wei = "0xde0b6b3a7640000"  # 1 QI en Wei
    price_hex  = await rpc_call(session, "quai_qiToQuai", [one_qi_wei, hdr["number"]])
    price_wei  = int(price_hex, 16) if price_hex else 0
    rate       = price_wei / 10**18   # QUAI por QI

    # 4) Equivalente en QUAI de minar Qi
    equiv_quai = qi_reward * rate

    # 5) Calcula la preferencia [0.0–1.0]
    total_quai = equiv_quai + quai_reward
    pref = (equiv_quai / total_quai) if total_quai > 0 else 0.0

    # 6) Mostrar resultados
    print(f"Bloque: {blk} (hex {hdr['number']})")
    print(f"Dificultad : {diff} (hex {hdr['difficulty']})\n")
    print(f"Reward Qi   : {qi_reward:.6f} QI/bloque")
    print(f"Reward Quai : {quai_reward:.6f} QUAI/bloque\n")
    print(f"Ratio on-chain      : 1 QI → {rate:.6f} QUAI")
    print(f"Equiv. minar Qi     : {equiv_quai:.6f} QUAI/bloque")
    print(f"Minar Quai directo  : {quai_reward:.6f} QUAI/bloque\n")
    print(f"Preferencia (0–1)   : {pref:.4f}\n")

    # 7) Ajustar preferencia en el nodo
    result = await rpc_call(session, "setMinerPreference", [pref])
    print("setMinerPreference result:", result)

async def run_controller():
    """Mantiene la suscripción WS y el bucle principal."""
    state = {"last_pref": None}
    backoff = 1

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with websockets.connect(RPC_WS) as ws:
                    print("✔ Suscrito a newHeads")
                    await ws.send(json.dumps({
                        "jsonrpc":"2.0",
                        "method":"eth_subscribe",
                        "params":["newHeads"],
                        "id":1
                    }))
                    await ws.recv()  # ack

                    async for raw in ws:
                        msg = json.loads(raw)
                        hdr = msg.get("params", {}).get("result", {})
                        if "number" in hdr and "difficulty" in hdr:
                            await process_block(hdr, session, state)

            except Exception as e:
                print(f"WS error: {e}; reintentando en {backoff}s…")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)  # backoff aumenta pero no pasa de 60s

def main():
    try:
        asyncio.run(run_controller())
    except KeyboardInterrupt:
        print("🛑 Terminando por Ctrl+C.")
        sys.exit(0)

if __name__ == "__main__":
    main()

