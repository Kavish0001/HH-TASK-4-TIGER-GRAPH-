"""Builds the document corpus: policy, pattern descriptions, and closed-case notes.

Three sources, one chunk type, every chunk citable. Nothing goes into a prompt
without a `ref` the explanation can point at.

The closed-case notes matter most. There are 5,565 of them, they are the only
place an outcome is written down, and the nine labelled `undocumented` are the
ones that solve the two hardest cases in the pack, so they are chunked
individually rather than summarised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backend.config import get_settings


@dataclass
class Chunk:
    ref: str
    text: str
    kind: str  # policy | pattern | closed_case
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_sections(markdown: str) -> list[tuple[str, str]]:
    """Split on headings, keeping the heading as the section name."""
    parts: list[tuple[str, str]] = []
    current_name = "preamble"
    buffer: list[str] = []
    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            if buffer:
                parts.append((current_name, "\n".join(buffer).strip()))
            current_name = heading.group(2).strip()
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        parts.append((current_name, "\n".join(buffer).strip()))
    return [(n, t) for n, t in parts if t]


def _window(text: str, max_chars: int = 1400) -> Iterable[str]:
    """Paragraph-aware splitting, so a rule never lands half in one chunk."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    buffer: list[str] = []
    size = 0
    for para in paragraphs:
        if size + len(para) > max_chars and buffer:
            yield "\n\n".join(buffer)
            buffer, size = [], 0
        buffer.append(para)
        size += len(para)
    if buffer:
        yield "\n\n".join(buffer)


def chunk_readme() -> list[Chunk]:
    """The Fraud Policy and the five pattern descriptions, from data/README.md."""
    path: Path = get_settings().data_dir / "README.md"
    text = path.read_text(encoding="utf-8")
    chunks: list[Chunk] = []
    for name, body in _split_sections(text):
        lowered = name.lower()
        if lowered.startswith("the five known fraud patterns"):
            kind = "pattern"
        elif any(
            lowered.startswith(prefix)
            for prefix in (
                "fraud policy",
                "0. what the agent starts with",
                "1. actions",
                "2. approval routing",
                "3. rules",
                "3a.",
                "3b.",
                "4. exposure",
                "5. gathering",
                "6. stopping",
                "7. explaining",
            )
        ):
            kind = "policy"
        elif lowered.startswith("regulatory references"):
            kind = "policy"
        elif lowered.startswith("things to know"):
            kind = "policy"
        else:
            continue
        for i, piece in enumerate(_window(body)):
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:48]
            chunks.append(
                Chunk(
                    ref=f"document:README#{slug}" + (f"[{i}]" if i else ""),
                    text=f"{name}\n\n{piece}",
                    kind=kind,
                    metadata={"section": name},
                )
            )

    # The five patterns are numbered paragraphs inside one section. Split them
    # out so a retrieval for "card testing" returns the definition, not the set.
    pattern_section = next(
        (body for name, body in _split_sections(text) if name.lower().startswith("the five known")),
        "",
    )
    for match in re.finditer(r"\*\*(\d)\.\s+([^.*]+)\.\*\*\s*(.+?)(?=\n\*\*\d\.|\Z)", pattern_section, re.S):
        number, title, body = match.group(1), match.group(2).strip(), match.group(3).strip()
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        chunks.append(
            Chunk(
                ref=f"document:README#pattern_{number}_{slug}",
                text=f"Known fraud pattern {number}: {title}. {body}",
                kind="pattern",
                metadata={"pattern_number": number, "title": title},
            )
        )
    return chunks


def chunk_policy_yaml() -> list[Chunk]:
    """The machine-readable policy, one chunk per rule, for exact citation."""
    path = get_settings().contracts_dir / "policy.yaml"
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    chunks: list[Chunk] = []
    for rule_id, rule in (data.get("rules") or {}).items():
        body = "\n".join(f"{k}: {v}" for k, v in rule.items())
        chunks.append(
            Chunk(
                ref=f"policy:{rule_id}",
                text=f"Policy rule {rule_id}. {rule.get('title', '')}\n{body}",
                kind="policy",
                metadata={"rule": rule_id, "title": rule.get("title", "")},
            )
        )
    chunks.append(
        Chunk(
            ref="policy:routing",
            text="Approval routing.\n"
            + "\n".join(f"{k}: {v}" for k, v in (data.get("routing") or {}).items()),
            kind="policy",
            metadata={"section": "routing"},
        )
    )
    chunks.append(
        Chunk(
            ref="policy:sar",
            text="Suspicious activity report triggers.\n"
            + "\n".join(f"{k}: {v}" for k, v in (data.get("sar") or {}).items()),
            kind="policy",
            metadata={"section": "sar"},
        )
    )
    return chunks


def chunk_closed_cases() -> list[Chunk]:
    """One chunk per closed case, keyed by case_id so retrieval is citable.

    `opened_at` is carried in the metadata because a case may only be used as
    memory by an investigation that starts after it closed.
    """
    path = get_settings().data_dir / "closed_cases_history.csv"
    df = pd.read_csv(path)
    chunks: list[Chunk] = []
    for _, row in df.iterrows():
        entities = [str(row["customer_id"]), str(row["card_id"])]
        connected = [c for c in str(row.get("connected_card_ids") or "").split("|") if c]
        entities.extend(connected)
        text = (
            f"Closed case {row['case_id']}, outcome {row['outcome']}, pattern {row['pattern']}, "
            f"exposure ${float(row['exposure_usd']):,.2f} over {int(row['n_txns'])} transaction(s), "
            f"opened {row['opened_at']}, closed {row['closed_at']}, "
            f"actions {row['actions_taken']}, report filed {row['report_filed']}.\n"
            f"{row['analyst_notes']}"
        )
        chunks.append(
            Chunk(
                ref=f"case:{row['case_id']}",
                text=text,
                kind="closed_case",
                metadata={
                    "case_id": str(row["case_id"]),
                    "outcome": str(row["outcome"]),
                    "pattern": str(row["pattern"]),
                    "exposure_usd": float(row["exposure_usd"]),
                    "closed_at": str(row["closed_at"]),
                    "opened_at": str(row["opened_at"]),
                    "entities": entities,
                    "txn_ids": [t for t in str(row.get("txn_ids") or "").split("|") if t],
                    "connected_card_ids": connected,
                },
            )
        )
    return chunks


def build_corpus() -> list[Chunk]:
    return chunk_readme() + chunk_policy_yaml() + chunk_closed_cases()
