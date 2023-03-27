import asyncio

from lcm_websocket_server import app


def main():
    asyncio.run(app.main())
