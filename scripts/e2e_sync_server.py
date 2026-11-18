"""Serve a disposable cloud sync ledger for the cross-device browser test."""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from ainovel.cloud_sync import create_sync_app


if __name__ == "__main__":
    with TemporaryDirectory(prefix="ainovel-sync-e2e-") as folder:
        app = create_sync_app(
            Path(folder) / "sync.sqlite",
            "e2e-test-only-provisioning-token",
            ("http://127.0.0.1:8877",),
        )
        uvicorn.run(app, host="127.0.0.1", port=8888, access_log=False)
