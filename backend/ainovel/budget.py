"""Shared provider budgets are kept outside the restorable manuscript database."""

from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import sqlite3
from time import time

from .db import DomainError, uid
from .settings import CAPS


def micro(value):
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class Budget:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir / "usage.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,account TEXT NOT NULL,
                provider TEXT NOT NULL,model TEXT NOT NULL,currency TEXT NOT NULL,reserved INTEGER NOT NULL,
                charged INTEGER,status TEXT NOT NULL,input_tokens INTEGER,output_tokens INTEGER,
                input_rate TEXT NOT NULL,output_rate TEXT NOT NULL,created REAL NOT NULL)""")
            db.execute(
                "CREATE TABLE IF NOT EXISTS call_metadata(call_id TEXT PRIMARY KEY,actual_model TEXT,request_id TEXT)"
            )

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, provider, task_id, input_upper, max_output, run_limit=None):
        if provider.input_rate is None or provider.output_rate is None:
            raise DomainError("unpriced", "该模型没有有效单价，已阻止付费调用。请配置 pricing。")
        amount = int(
            (
                Decimal(input_upper) * provider.input_rate + Decimal(max_output) * provider.output_rate
            ).to_integral_value(rounding=ROUND_CEILING)
        )
        with self.db(True) as db:
            spent = db.execute(
                "SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls WHERE provider=?",
                (provider.kind,),
            ).fetchone()[0]
            if spent + amount > micro(self.settings.limits[provider.kind]):
                raise DomainError("budget", "服务商共享预算不足，已停止新请求。", 409)
            own = db.execute(
                "SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            if run_limit is not None and own + amount > micro(run_limit):
                raise DomainError("budget", "本次任务预算不足，已保存进度。", 409)
            call_id = uid()
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    call_id,
                    task_id,
                    provider.id,
                    provider.kind,
                    provider.model,
                    CAPS[provider.kind][0],
                    amount,
                    None,
                    "reserved",
                    None,
                    None,
                    str(provider.input_rate),
                    str(provider.output_rate),
                    time(),
                ),
            )
            return call_id

    def metadata(self, call_id, model, request_id):
        with self.db(True) as db:
            db.execute("INSERT OR REPLACE INTO call_metadata VALUES(?,?,?)", (call_id, model, request_id))

    def settle(self, call_id, usage=None, known_no_charge=False):
        with self.db(True) as db:
            call = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if not call or call["status"] == "settled":
                return
            if known_no_charge:
                cost, status, ins, outs = 0, "settled", 0, 0
            elif usage and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input", "output")):
                ins, outs = usage["input"], usage["output"]
                cost = int(
                    (
                        Decimal(ins) * Decimal(call["input_rate"])
                        + Decimal(outs) * Decimal(call["output_rate"])
                    ).to_integral_value(rounding=ROUND_CEILING)
                )
                status = "settled"
            else:
                cost, status, ins, outs = None, "unknown", None, None
            db.execute(
                "UPDATE calls SET charged=?,status=?,input_tokens=?,output_tokens=? WHERE id=?",
                (cost, status, ins, outs, call_id),
            )

    def report(self):
        with self.db() as db:
            groups = []
            for kind, (currency, _) in CAPS.items():
                rows = [
                    dict(row)
                    for row in db.execute(
                        "SELECT c.*,m.actual_model,m.request_id FROM calls c LEFT JOIN call_metadata m ON m.call_id=c.id WHERE provider=? ORDER BY created DESC",
                        (kind,),
                    )
                ]
                spent = sum(row["charged"] or 0 for row in rows)
                held = sum(row["reserved"] for row in rows if row["charged"] is None)
                groups.append(
                    {
                        "provider": kind,
                        "currency": currency,
                        "limit": str(self.settings.limits[kind]),
                        "settled": spent / 1_000_000,
                        "reserved_unknown": held / 1_000_000,
                        "remaining": max(0, float(self.settings.limits[kind]) - (spent + held) / 1_000_000),
                        "calls": rows[:200],
                    }
                )
            return groups
