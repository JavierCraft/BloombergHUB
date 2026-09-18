"""Tech-market intelligence: model releases via the HuggingFace Hub.

Source: huggingface/huggingface_hub (★3.871, pushed 2026-09-05).

The audit's read on this category is the useful one: tech markets resolve on
*events* — a model ships, a benchmark falls, a product is announced — so the
edge is detection speed, not modelling skill. A model's weights appear on the
Hub minutes after upload, frequently before the announcement blog post.

Resolution warning worth repeating in code: a tech market that names a specific
leaderboard settles on that leaderboard's number, even when everyone knows the
real answer differs. Read the resolution rules before the model.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config import HF_TOKEN  # noqa: E402


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


class ModelTracker:
    """Detect new and moving models on the Hub."""

    def __init__(self):
        try:
            from huggingface_hub import HfApi
        except ImportError:
            from src.core.errors import MissingDependency

            raise MissingDependency("huggingface-hub", "huggingface_hub") from None
        self.api = HfApi(token=HF_TOKEN or None)

    # -- detection ----------------------------------------------------------

    def new_models(self, hours: int = 24, limit: int = 100) -> list[dict]:
        """Models *created* in the last N hours, newest first.

        Sorting by `createdAt` rather than `lastModified` matters: a README typo
        fix on a two-year-old checkpoint bumps lastModified and is not news.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows: list[dict] = []

        for model in self.api.list_models(sort="createdAt", limit=limit):
            created = getattr(model, "created_at", None)
            if created and created < since:
                break
            rows.append({
                "id": model.id,
                "author": getattr(model, "author", None) or model.id.split("/")[0],
                "pipeline_tag": getattr(model, "pipeline_tag", None),
                "created_at": _iso(created),
                "last_modified": _iso(getattr(model, "last_modified", None)),
                "downloads": getattr(model, "downloads", 0) or 0,
                "likes": getattr(model, "likes", 0) or 0,
                "url": f"https://huggingface.co/{model.id}",
            })
        return rows

    def trending(self, limit: int = 30) -> list[dict]:
        """What the Hub itself is surfacing right now."""
        return [
            {
                "id": m.id,
                "author": getattr(m, "author", None) or m.id.split("/")[0],
                "pipeline_tag": getattr(m, "pipeline_tag", None),
                "downloads": getattr(m, "downloads", 0) or 0,
                "likes": getattr(m, "likes", 0) or 0,
                "last_modified": _iso(getattr(m, "last_modified", None)),
                "url": f"https://huggingface.co/{m.id}",
            }
            for m in self.api.list_models(sort="trendingScore", limit=limit)
        ]

    def watch_authors(self, authors: list[str], hours: int = 168, per_author: int = 10) -> list[dict]:
        """Releases from the labs whose names appear in market questions.

        A market asking "will Lab X ship a model before date Y" resolves on this
        list, so watching a handful of authors beats scanning the whole Hub.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows: list[dict] = []

        for author in authors:
            try:
                models = self.api.list_models(
                    author=author, sort="createdAt", limit=per_author
                )
            except Exception:
                continue
            for model in models:
                created = getattr(model, "created_at", None)
                if created and created < since:
                    continue
                rows.append({
                    "author": author,
                    "id": model.id,
                    "pipeline_tag": getattr(model, "pipeline_tag", None),
                    "created_at": _iso(created),
                    "downloads": getattr(model, "downloads", 0) or 0,
                    "likes": getattr(model, "likes", 0) or 0,
                    "url": f"https://huggingface.co/{model.id}",
                })

        rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
        return rows

    def search_models(self, query: str, limit: int = 25) -> list[dict]:
        return [
            {
                "id": m.id,
                "pipeline_tag": getattr(m, "pipeline_tag", None),
                "downloads": getattr(m, "downloads", 0) or 0,
                "likes": getattr(m, "likes", 0) or 0,
                "last_modified": _iso(getattr(m, "last_modified", None)),
                "url": f"https://huggingface.co/{m.id}",
            }
            for m in self.api.list_models(search=query, limit=limit)
        ]

    def new_datasets(self, hours: int = 48, limit: int = 50) -> list[dict]:
        """New benchmark datasets — often the resolution source of a tech market."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows: list[dict] = []
        for ds in self.api.list_datasets(sort="createdAt", limit=limit):
            created = getattr(ds, "created_at", None)
            if created and created < since:
                break
            rows.append({
                "id": ds.id,
                "author": getattr(ds, "author", None) or ds.id.split("/")[0],
                "created_at": _iso(created),
                "downloads": getattr(ds, "downloads", 0) or 0,
                "likes": getattr(ds, "likes", 0) or 0,
                "url": f"https://huggingface.co/datasets/{ds.id}",
            })
        return rows

    def model_info(self, model_id: str) -> dict:
        info = self.api.model_info(model_id)
        return {
            "id": info.id,
            "pipeline_tag": getattr(info, "pipeline_tag", None),
            "tags": list(getattr(info, "tags", []) or []),
            "downloads": getattr(info, "downloads", 0) or 0,
            "likes": getattr(info, "likes", 0) or 0,
            "created_at": _iso(getattr(info, "created_at", None)),
            "last_modified": _iso(getattr(info, "last_modified", None)),
            "gated": getattr(info, "gated", False),
            "url": f"https://huggingface.co/{info.id}",
        }


# Labs whose releases actually appear in prediction markets.
WATCHED_AUTHORS = [
    "meta-llama", "mistralai", "Qwen", "google", "microsoft",
    "deepseek-ai", "allenai", "nvidia", "openai",
]
