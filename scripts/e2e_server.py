"""Isolated real API/Worker test server, with a deterministic offline model only."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import uvicorn

from ainovel.api import create_app
from ainovel.settings import Provider, Settings
from ainovel.worker import Worker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/tests"))
from test_reference_flow import Model  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="ainovel-browser-tests-") as location:
        settings = Settings(
            Path(location),
            {
                "offline": Provider(
                    "offline",
                    "deepseek",
                    "offline-fixture",
                    "https://example.invalid",
                    "offline-not-real",
                    Decimal("2"),
                    Decimal("8"),
                )
            },
            {"openai": Decimal("20"), "deepseek": Decimal("80")},
            test_mode=True,
            port=args.port,
        )
        app = create_app(settings)
        original = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            async with original(app):
                worker = Worker(settings, Model())

                async def execute():
                    while True:
                        if not await worker.run_once():
                            await asyncio.sleep(0.1)

                runner = asyncio.create_task(execute())
                try:
                    yield
                finally:
                    runner.cancel()
                    try:
                        await runner
                    except asyncio.CancelledError:
                        pass

        app.router.lifespan_context = lifespan
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
