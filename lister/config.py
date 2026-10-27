"""Settings: non-secret config from config.toml, secrets from .env / environment."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Seller:
    location: str = "Newfields, NH"
    postal_code: str = ""
    ebay_username: str = ""  # your own listings are excluded from comps
    shipping_profile: str = ""
    return_profile: str = ""
    payment_profile: str = ""
    merchant_location_key: str = "home"  # inventory location created for API publishing
    ebay_runame: str = ""
    ebay_refresh_token: str = ""


@dataclass(frozen=True)
class Listing:
    default_category: str = "books"
    format: str = "FixedPrice"
    duration: str = "GTC"
    best_offer: bool = True
    best_offer_auto_accept: float | None = None
    best_offer_minimum: float | None = None
    sku_prefix: str = "BK"


@dataclass(frozen=True)
class R2:
    bucket: str = ""
    public_base_url: str = ""
    prefix: str = "listings"
    account_id: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""

    @property
    def configured(self) -> bool:
        return all([self.bucket, self.public_base_url, self.account_id, self.access_key_id, self.secret_access_key])


@dataclass(frozen=True)
class Photos:
    max_edge_px: int = 1600
    jpeg_quality: int = 88


@dataclass(frozen=True)
class Models:
    identify: str = "claude-opus-5-5"
    price: str = "claude-opus-5-5"
    describe: str = "claude-opus-5-5"


@dataclass(frozen=True)
class Pricing:
    sources: tuple[str, ...] = ("ebay_active", "web_research")
    ebay_marketplace: str = "EBAY_US"
    max_active_comps: int = 40
    ebay_client_id: str = ""
    ebay_client_secret: str = ""


@dataclass(frozen=True)
class Pipeline:
    # "api": the background worker calls the Anthropic API for identify/price/describe.
    # "agent": queued items wait for a Claude Code / Codex session (see AGENTS.md).
    llm: str = "api"


@dataclass(frozen=True)
class Orchestrator:
    # `lister drive`: which CLI runs the role agents ("claude" or "codex"), and per-role
    # settings: {role: {"claude": model, "codex": model, "effort": level, "chrome": bool}}.
    engine: str = "claude"
    evaluator_engine: str = "codex"  # a different vendor checks the generator's work
    max_rounds: int = 3
    timeout_s: int = 1800
    roles: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Server:
    host: str = "0.0.0.0"
    port: int = 8765
    access_token: str = ""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    seller: Seller = field(default_factory=Seller)
    listing: Listing = field(default_factory=Listing)
    r2: R2 = field(default_factory=R2)
    photos: Photos = field(default_factory=Photos)
    models: Models = field(default_factory=Models)
    pricing: Pricing = field(default_factory=Pricing)
    pipeline: Pipeline = field(default_factory=Pipeline)
    server: Server = field(default_factory=Server)
    orchestrator: Orchestrator = field(default_factory=Orchestrator)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lister.db"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


def _section(raw: dict, name: str, cls, **extra):
    values = {k: v for k, v in raw.get(name, {}).items() if k in cls.__dataclass_fields__}
    if "sources" in values:
        values["sources"] = tuple(values["sources"])
    return cls(**values, **extra)


def load_settings(config_path: Path | None = None, data_dir: Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    path = config_path or Path(os.getenv("LISTER_CONFIG", ROOT / "config.toml"))
    raw: dict = {}
    if path.exists():
        with path.open("rb") as handle:
            raw = tomllib.load(handle)

    data = data_dir or Path(os.getenv("LISTER_DATA_DIR", ROOT / "data"))
    env = os.environ.get
    return Settings(
        data_dir=data,
        seller=_section(
            raw,
            "seller",
            Seller,
            ebay_runame=env("EBAY_RUNAME", ""),
            ebay_refresh_token=env("EBAY_REFRESH_TOKEN", ""),
        ),
        listing=_section(raw, "listing", Listing),
        r2=_section(
            raw,
            "r2",
            R2,
            account_id=env("R2_ACCOUNT_ID", ""),
            access_key_id=env("R2_ACCESS_KEY_ID", ""),
            secret_access_key=env("R2_SECRET_ACCESS_KEY", ""),
        ),
        photos=_section(raw, "photos", Photos),
        models=_section(raw, "models", Models),
        pricing=_section(
            raw,
            "pricing",
            Pricing,
            ebay_client_id=env("EBAY_CLIENT_ID", ""),
            ebay_client_secret=env("EBAY_CLIENT_SECRET", ""),
        ),
        pipeline=_section(raw, "pipeline", Pipeline),
        server=_section(raw, "server", Server, access_token=env("LISTER_ACCESS_TOKEN", "")),
        orchestrator=_section(raw, "orchestrator", Orchestrator),
    )
