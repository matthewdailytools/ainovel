"""Local configuration. Secrets never become public model/profile metadata."""

from dataclasses import dataclass, field
from decimal import Decimal
import os
from pathlib import Path
import tomllib

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CAPS = {"openai": ("USD", Decimal("20")), "deepseek": ("CNY", Decimal("80"))}


@dataclass(frozen=True)
class Provider:
    id: str
    kind: str
    model: str
    base_url: str
    key: str = field(repr=False)
    input_rate: Decimal | None = None
    output_rate: Decimal | None = None
    price_source: str = ""
    reasoning: str = "none"
    max_input_bytes: int = 120_000

    def public(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "model": self.model,
            "base_url": self.base_url,
            "configured": bool(self.key),
            "priced": self.input_rate is not None and self.output_rate is not None,
            "currency": CAPS[self.kind][0],
            "input_rate": str(self.input_rate),
            "output_rate": str(self.output_rate),
            "price_source": self.price_source,
        }


@dataclass
class Settings:
    data_dir: Path
    providers: dict[str, Provider]
    limits: dict[str, Decimal]
    test_mode: bool = False
    timeout: float = 300
    idle_timeout: float = 60
    port: int = 8765

    @classmethod
    def load(cls, data_dir: Path | None = None):
        load_dotenv(ROOT / ".env", override=False)
        config_path = Path(os.environ.get("AINOVEL_CONFIG", str(ROOT / "ainovel.toml")))
        config = tomllib.loads(config_path.read_text("utf-8")) if config_path.is_file() else {}
        location = (
            Path(
                data_dir
                or os.environ.get("AINOVEL_DATA_DIR")
                or config.get("data_dir")
                or (Path.home() / ".ainovel")
            )
            .expanduser()
            .resolve()
        )
        if location.is_relative_to(ROOT):
            raise ValueError("数据目录必须位于代码仓库之外。")
        if (location / "MIGRATED_TO.txt").exists():
            raise ValueError("此数据目录已迁移，请使用迁移记录中的新位置；旧费用账本不能重新作为活动账本。")
        # Built-in prices are deliberately absent: unknown tariffs cannot spend money.
        definitions = config.get(
            "accounts",
            {
                "openai_1": {"kind": "openai", "key_env": "OPENAI_API_KEY"},
                "openai_2": {"kind": "openai", "key_env": "OPENAI_API_KEY_2"},
                "deepseek": {"kind": "deepseek", "key_env": "DEEPSEEK_API_KEY"},
            },
        )
        providers = {}
        for account, item in definitions.items():
            kind = item["kind"]
            if kind not in CAPS:
                raise ValueError("Unsupported provider kind")
            prefix = kind.upper()
            model = (
                item.get("model")
                or os.getenv(prefix + "_MODEL")
                or ("gpt-6-luna" if kind == "openai" else "deepseek-flash")
            )
            tariff = config.get("pricing", {}).get(model, {})
            rates = [
                Decimal(str(tariff[key])) if key in tariff else None
                for key in ("input_per_million", "output_per_million")
            ]
            if any(rate is not None and (not rate.is_finite() or rate <= 0) for rate in rates):
                raise ValueError("Invalid tariff")
            base_url = (
                item.get("base_url")
                or os.getenv(prefix + "_BASE_URL")
                or ("https://api.openai.com/v1" if kind == "openai" else "https://api.deepseek.com")
            )
            from urllib.parse import urlsplit

            url = urlsplit(base_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Provider URL must be an HTTPS base URL without credentials")
            if tariff and tariff.get("currency") != CAPS[kind][0]:
                raise ValueError("Tariff currency does not match shared budget")
            providers[account] = Provider(
                account,
                kind,
                model,
                base_url.rstrip("/"),
                os.getenv(item["key_env"], ""),
                *rates,
                tariff.get("source", ""),
                os.getenv(prefix + "_REASONING_EFFORT", "none"),
            )
        limits = {
            kind: min(cap, Decimal(str(config.get("budgets", {}).get(kind, cap))))
            for kind, (_, cap) in CAPS.items()
        }
        if any(not value.is_finite() or value <= 0 for value in limits.values()):
            raise ValueError("Invalid budget")
        port = int(os.environ.get("AINOVEL_PORT", "8765"))
        if not 1024 <= port <= 65535:
            raise ValueError("Local port must be between 1024 and 65535")
        return cls(location.resolve(), providers, limits, port=port)
