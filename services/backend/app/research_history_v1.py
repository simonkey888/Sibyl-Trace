from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.evidence_v1 import HistoryEvidence, history_evidence
from app.polymarket import PolymarketClient


@dataclass(frozen=True)
class ResearchHistory:
    rows: list[Any]
    evidence: HistoryEvidence


def fetch_research_history(
    client: PolymarketClient,
    wallet: str,
    *,
    limit: int = 1000,
    page_size: int = 50,
) -> ResearchHistory:
    target = min(max(limit, 0), 5000)
    if target == 0:
        evidence = history_evidence(
            [],
            requested_limit=0,
            page_size=page_size,
            source_order="TIMESTAMP_DESC",
            source_payload=[],
        )
        return ResearchHistory([], evidence)

    rows: list[Any] = []
    pages: list[list[Any]] = []
    transport_complete = True

    for offset in range(0, target, page_size):
        current_limit = min(page_size, target - offset)
        try:
            data = client._get(  # noqa: SLF001 - research adapter needs raw evidence.
                f"{client.settings.data_api_base}/closed-positions",
                {
                    "user": wallet,
                    "limit": current_limit,
                    "offset": offset,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "DESC",
                },
            )
        except Exception:
            transport_complete = False
            break

        if not isinstance(data, list):
            transport_complete = False
            break

        page = list(data)
        pages.append(page)
        rows.extend(page)
        if len(page) < current_limit:
            break

    typed_pages = [
        page for page in pages if all(isinstance(item, dict) for item in page)
    ]
    evidence = history_evidence(
        typed_pages,
        requested_limit=target,
        page_size=page_size,
        source_order="TIMESTAMP_DESC",
        source_payload=pages,
        transport_complete=transport_complete and len(typed_pages) == len(pages),
    )
    return ResearchHistory(rows, evidence)
