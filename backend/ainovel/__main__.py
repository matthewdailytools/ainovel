import argparse
import json
import os
from pathlib import Path
import subprocess
import socket
import sys

from .settings import ROOT, Settings


def main():
    parser = argparse.ArgumentParser(
        description="ainovel 本机网页：serve / schema / backup / verify / restore"
    )
    parser.add_argument(
        "command", nargs="?", default="serve", choices=("serve", "schema", "backup", "verify", "restore")
    )
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    settings = Settings(ROOT, {}, {}) if args.command == "schema" else Settings.load()
    if args.command != "schema":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "schema":
        from .api import create_app

        app = create_app(settings, schema_only=True)

        body = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
        if args.file:
            args.file.write_text(body, encoding="utf-8", newline="\n")
        else:
            print(body)
    elif args.command == "serve":
        if not (ROOT / "dist/index.html").is_file():
            parser.error("请先执行 npm run build。")
        import uvicorn
        from .api import create_app
        from .db import uid

        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", settings.port))
            except OSError:
                parser.error("本机端口已占用；请关闭已有服务或更换端口，未启动新 Worker。")
        runtime_dir = settings.data_dir / "runtime"
        runtime_dir.mkdir(exist_ok=True)
        stop_file = runtime_dir / (uid() + ".stop")

        with (settings.data_dir / "worker.log").open("a", encoding="utf-8") as log:
            worker = subprocess.Popen(
                [sys.executable, "-m", "ainovel.worker"],
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env={
                    **os.environ,
                    "AINOVEL_PARENT_PID": str(os.getpid()),
                    "AINOVEL_WORKER_STOP_FILE": str(stop_file),
                },
            )
            try:
                uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, access_log=False)
            finally:
                stop_file.write_text("stop", encoding="utf-8")
                try:
                    worker.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    worker.terminate()
                    worker.wait(timeout=5)
    else:
        from .backups import create_backup, restore_backup, verify_backup
        from .db import Database
        from .library import Library

        db = Database(settings.data_dir / "manuscripts.sqlite")
        Library(db, settings.data_dir)
        if args.command == "backup":
            result = create_backup(settings, db)
        elif not args.file:
            parser.error("需要 --file 备份文件路径")
        else:
            result = (
                verify_backup(args.file)
                if args.command == "verify"
                else restore_backup(settings, db, args.file)
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
