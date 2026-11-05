import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///invoiceops.db"
    provider: str = "baseline"
    fixture_manifest: Path = ROOT / "data/synthetic/manifest.json"
    report: Path = ROOT / "data/evaluation/metrics.json"
    frontend: Path = ROOT / "frontend/dist"
    max_bytes: int = 8 * 1024 * 1024
    max_pages: int = 20

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            provider=os.getenv("INVOICE_PROVIDER", cls.provider),
            fixture_manifest=Path(os.getenv("INVOICE_FIXTURE_MANIFEST", str(cls.fixture_manifest))),
            report=Path(os.getenv("INVOICE_EVAL_REPORT", str(cls.report))),
        )

