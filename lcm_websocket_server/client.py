import asyncio

from websockets import connect

from lcm_websocket_server.log import get_logger
logger = get_logger(__name__)


async def main():
    async with connect("ws://localhost:8765") as websocket:
        while True:
            message = await websocket.recv()
            logger.info(f"Received {message[:10]}...{message[-10:]}")


if __name__ == "__main__":
    asyncio.run(main())