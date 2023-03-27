import asyncio
from websockets import connect


async def main():
    async with connect("ws://localhost:8765") as websocket:
        while True:
            message = await websocket.recv()
            print(f"Received {message[:10]}...{message[-10:]}")


if __name__ == "__main__":
    asyncio.run(main())