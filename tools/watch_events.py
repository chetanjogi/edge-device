import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://localhost:8000/events") as ws:
        async for msg in ws:
            e = json.loads(msg)
            if e["type"] == "reading":
                print(f"[{e['status']}] T={e['T']:.1f} P={e['P']:.1f} H={e['H']:.1f}")
            else:
                print(e)

asyncio.run(main())