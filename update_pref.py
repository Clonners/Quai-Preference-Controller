
#!/usr/bin/env python3
import asyncio
import json
import math
import sys
import aiohttp

RPC_HTTP = "http://127.0.0.1:9001"

# Coeficientes de emisión (Token Dynamics)
K_QI   = 1 / (8 * 10**9)  # Qi: lineal
K_QUAI = 1.0              # Quai: logarítmico

async def rpc_call(session, method, params):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    async with session.post(RPC_HTTP, json=payload) as resp:
        data = await resp.json()
        return data.get("result")

async def main():
    async with aiohttp.ClientSession() as session:
        # 1) Obtener header del último bloque
        header = await rpc_call(session, "quai_getBlockByNumber", ["latest", False])
        if not header or "difficulty" not in header or "number" not in header:
            print("Error al obtener último bloque.")
            sys.exit(1)

        blk_hex  = header["number"]       # e.g. "0x2eb17"
        diff_hex = header["difficulty"]   # e.g. "0x491f…"
        blk  = int(blk_hex, 16)
        diff = int(diff_hex, 16)

        # 2) Calcular reward por bloque
        qi_reward   = K_QI   * diff
        quai_reward = K_QUAI * math.log2(diff)

        # 3) Consultar ratio on-chain Qi → Quai para este bloque
        one_qi_wei = "0xde0b6b3a7640000"  # 1 QI en Wei
        price_hex  = await rpc_call(session, "quai_qiToQuai", [one_qi_wei, blk_hex])
        price_wei  = int(price_hex, 16) if price_hex else 0
        rate       = price_wei / 10**18   # QUAI por QI

        # 4) Equivalente en QUAI de minar Qi
        equiv_quai = qi_reward * rate

        # 5) Calcula la preferencia [0.0–1.0]
        total_quai = equiv_quai + quai_reward
        pref = (equiv_quai / total_quai) if total_quai > 0 else 0.0

        # 6) Mostrar resultados
        print(f"Bloque: {blk} (hex {blk_hex})")
        print(f"Dificultad : {diff} (hex {diff_hex})\n")
        print(f"Reward Qi   : {qi_reward:.6f} QI/bloque")
        print(f"Reward Quai : {quai_reward:.6f} QUAI/bloque\n")
        print(f"Ratio on-chain      : 1 QI → {rate:.6f} QUAI")
        print(f"Equiv. minar Qi     : {equiv_quai:.6f} QUAI/bloque")
        print(f"Minar Quai directo  : {quai_reward:.6f} QUAI/bloque\n")
        print(f"Preferencia (0–1)   : {pref:.4f}\n")

        # 7) Ajustar preferencia en el nodo
        result = await rpc_call(session, "setMinerPreference", [pref])
        print("setMinerPreference result:", result)

if __name__ == "__main__":
    asyncio.run(main())
