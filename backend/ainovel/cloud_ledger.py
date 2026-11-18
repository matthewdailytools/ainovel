"""Persistent identity for each independent copy of the cloud sync ledger."""

import secrets
import sqlite3
from typing import Annotated

from fastapi import Header
from pydantic import StringConstraints

from .db import DomainError

LEDGER_HEADER = "X-Ainovel-Ledger"
LedgerHeader = Annotated[str | None, Header(alias=LEDGER_HEADER)]
LedgerId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
DB_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_ledger(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 ledger_id TEXT NOT NULL CHECK(length(ledger_id)=32 AND ledger_id NOT GLOB '*[^0-9a-f]*'),
 requires_header INTEGER NOT NULL CHECK(requires_header IN (0,1)));
"""


def initialize(tx, *, requires_header):
    tx.execute("INSERT OR IGNORE INTO sync_ledger VALUES(1,?,?)", (secrets.token_hex(16), requires_header))


def identity(tx):
    try:
        row = tx.execute("SELECT ledger_id,requires_header FROM sync_ledger WHERE singleton=1").fetchone()
    except sqlite3.Error as error:
        raise DomainError("sync_storage", "同步账本身份暂不可核验。", 503) from error
    if not row:
        raise DomainError("sync_storage", "同步账本身份暂不可核验。", 503)
    ledger_id, requires_header = row
    if (
        not isinstance(ledger_id, str)
        or len(ledger_id) != 32
        or any(char not in "0123456789abcdef" for char in ledger_id)
        or requires_header not in (0, 1)
    ):
        raise DomainError("sync_storage", "同步账本身份暂不可核验。", 503)
    return ledger_id, bool(requires_header)


def verify_header(tx, expected, *, receipt_lookup=False):
    ledger_id, requires_header = identity(tx)
    if expected != ledger_id and (expected is not None or (requires_header and not receipt_lookup)):
        raise DomainError(
            "sync_ledger_replaced",
            "当前云账本与本机连接的账本不一致。本机作品和待同步记录已保留，请使用独立作品副本连接当前云端。",
            409,
        )


def rotate(tx):
    tx.execute(
        "UPDATE sync_ledger SET ledger_id=?,requires_header=1 WHERE singleton=1", (secrets.token_hex(16),)
    )
