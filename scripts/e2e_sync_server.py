"""Serve a disposable cloud sync ledger for the cross-device browser test."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
import uvicorn

from ainovel.cloud_sync import create_sync_app
from ainovel.cloud_sync_backup import create_backup, restore_backup

PROVISIONING = "e2e-test-only-provisioning-token"


def test_sync_app(folder, browser_origin):
    """Route the same synthetic origin to a restored, independent SQLite ledger."""
    initial_database = folder / "sync.sqlite"
    initial_app = create_sync_app(initial_database, PROVISIONING, (browser_origin,))
    database, current = initial_database, initial_app
    backup = None
    app = FastAPI()

    def require_control(authorization):
        if authorization != f"Bearer {PROVISIONING}":
            raise HTTPException(403)

    @app.post("/__e2e/ledger/backup")
    def capture(authorization: Annotated[str, Header()] = ""):
        nonlocal backup
        require_control(authorization)
        target = folder / f"backup-{uuid4().hex}.sqlite"
        outcome = create_backup(database, target)
        backup = target
        return outcome

    @app.post("/__e2e/ledger/restore")
    def restore(authorization: Annotated[str, Header()] = ""):
        nonlocal database, current
        require_control(authorization)
        if backup is None:
            raise HTTPException(409, "Capture a synthetic ledger backup first")
        target = folder / f"restored-{uuid4().hex}.sqlite"
        outcome = restore_backup(backup, target)
        next_app = create_sync_app(target, PROVISIONING, (browser_origin,))
        database, current = target, next_app
        return outcome

    @app.post("/__e2e/ledger/finish", status_code=204)
    def finish(authorization: Annotated[str, Header()] = ""):
        nonlocal backup, database, current
        require_control(authorization)
        backup, database, current = None, initial_database, initial_app

    async def active_ledger(scope, receive, send):
        await current(scope, receive, send)

    app.mount("/", active_ledger)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--browser-origin", default="http://127.0.0.1:8877")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="ainovel-sync-e2e-") as folder:
        app = test_sync_app(Path(folder), args.browser_origin)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
