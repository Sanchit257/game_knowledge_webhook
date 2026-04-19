"""Parse Marvel RPG PDFs into JSON knowledge files and the unified knowledge base."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber

from app import database
from config import KNOWLEDGE_DIR, PDF_DIR

logger = logging.getLogger(__name__)

STAT_LABELS: tuple[str, ...] = (
    "MELEE",
    "AGILITY",
    "RESILIENCE",
    "VIGILANCE",
    "EGO",
    "LOGIC",
)

CHARACTER_PROFILES_JSON: Path = KNOWLEDGE_DIR / "character_profiles.json"
CHARACTER_SHEETS_JSON: Path = KNOWLEDGE_DIR / "character_sheets.json"
ERRATA_JSON: Path = KNOWLEDGE_DIR / "errata.json"
KNOWLEDGE_BASE_JSON: Path = KNOWLEDGE_DIR / "knowledge_base.json"

_PROFILES_PDF_KEY = "character_profiles"
_SHEETS_PDF_KEY = "character_sheets"
_ERRATA_PDF_KEY = "errata"


def _utc_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""

    return datetime.now(timezone.utc).isoformat()


def _ensure_knowledge_dir() -> None:
    """Create ``data/knowledge`` if missing."""

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: Any) -> None:
    """Serialize ``data`` to ``path`` as UTF-8 JSON."""

    _ensure_knowledge_dir()
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _normalize_key(name: str) -> str:
    """Normalize a character name for merging (case- and space-insensitive)."""

    return re.sub(r"\s+", " ", name.strip()).casefold()



def _faction_from_chunk(chunk: str) -> str:
    """Prefer ``Origin:`` line value; fall back to ``Teams:`` block."""

    for line in chunk.splitlines():
        stripped = line.strip()
        if stripped.startswith("Origin:"):
            return re.sub(r"\s+", " ", stripped.split(":", 1)[1].strip())
    tm = re.search(r"Teams:\s*(.+?)(?:\nBase:)", chunk, re.S)
    if tm:
        return re.sub(r"\s+", " ", tm.group(1).strip())
    return ""


def _sheet_name_plausible(raw: str) -> bool:
    """Reject blank-form template text mistaken for a character name."""

    if len(raw) < 2 or len(raw) > 100:
        return False
    upper = raw.upper()
    deny = (
        "ABILITIES",
        "DAMAGE",
        "HEIGHT:",
        "WEIGHT:",
        "GENDER:",
        "TRAITS",
        "POWERS",
        "NAME:",
        "CODENAME:",
        "ABILITY",
        "DEFENSE",
    )
    if any(x in upper for x in deny):
        return False
    if raw.count(":") >= 2:
        return False
    return True


def _stats_from_tables(page: Any) -> dict[str, int]:
    """Try to read ability scores from vector tables when present."""

    stats: dict[str, int] = {}
    try:
        tables = page.extract_tables() or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("extract_tables failed: %s", exc)
        return stats

    for table in tables:
        if not table:
            continue
        for row in table:
            if not row or len(row) < 2:
                continue
            label = (row[0] or "").strip().upper()
            if label not in STAT_LABELS:
                continue
            for cell in row[1:]:
                if cell is None:
                    continue
                m = re.search(r"(\d+)", str(cell))
                if m:
                    stats[label.lower()] = int(m.group(1))
                    break
    return stats


def extract_stats_from_page(page: Any) -> dict[str, int]:
    """
    Read ability scores from a character profile page using word positions.

    Returns lowercase keys: melee, agility, resilience, vigilance, ego, logic.
    """

    stats: dict[str, int] = {}
    try:
        words = page.extract_words(use_text_flow=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_words failed: %s", exc)
        return stats

    for label in STAT_LABELS:
        label_word = next((w for w in words if w["text"] == label), None)
        if label_word is None:
            continue
        y0 = float(label_word["top"])
        candidates = [
            w
            for w in words
            if y0 < float(w["top"]) < y0 + 50 and 45 <= float(w["x0"]) < 105
        ]
        candidates.sort(key=lambda w: (float(w["top"]), float(w["x0"])))
        for w in candidates:
            if re.fullmatch(r"\d+", w["text"]):
                stats[label.lower()] = int(w["text"])
                break
    if len(stats) < 4:
        return {**_stats_from_tables(page), **stats}
    return stats


def _parse_profile_chunk(
    raw_name: str,
    chunk: str,
    pdf_path: Path,
    page_index_zero_based: int,
) -> dict[str, Any]:
    """Build one character dict from a biography chunk and first page index."""

    name = raw_name.strip()
    source_page = page_index_zero_based + 1

    rank: int | None = None
    rm = re.search(r"^\s*(\d+)\s*[—\-]\s*Height", chunk, re.M)
    if rm:
        rank = int(rm.group(1))
    else:
        rm2 = re.search(r"^\s*(\d+)\s+(\d+)\s+Height", chunk, re.M)
        if rm2:
            rank = int(rm2.group(1))

    faction = _faction_from_chunk(chunk)

    abilities: list[str] = []
    am = re.search(
        r"ABILITIES\s*(.*?)(?=TRAITS\s*&\s*TAGS)",
        chunk,
        re.S | re.I,
    )
    if am:
        for m in re.finditer(
            r"(?m)^([A-Za-z][A-Za-z /]+):\s*(\d+)",
            am.group(1),
        ):
            abilities.append(f"{m.group(1)}: {m.group(2)}")

    description = ""
    hm = re.search(
        r"History\s*(.*?)(?=ABILITIES|TRAITS\s*&\s*TAGS|POWERS|\Z)",
        chunk,
        re.S | re.I,
    )
    if hm:
        description = re.sub(r"\s+", " ", hm.group(1).strip())

    stats: dict[str, int] = {}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if 0 <= page_index_zero_based < len(pdf.pages):
                stats = extract_stats_from_page(pdf.pages[page_index_zero_based])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stats extraction failed for %s p%s: %s", name, source_page, exc)

    return {
        "name": name,
        "faction": faction,
        "rank": rank,
        "abilities": abilities,
        "stats": stats,
        "description": description,
        "source_page": source_page,
    }



def parse_character_profiles(pdf_path: Path) -> list[dict[str, Any]]:
    """
    Parse ``MMRPG_CharacterProfiles_*.pdf`` into a list of character dicts.

    Writes ``data/knowledge/character_profiles.json`` and returns the list.
    """

    characters: list[dict[str, Any]] = []
    full_parts: list[str] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning("extract_text failed page %s: %s", i + 1, exc)
                    text = ""
                full_parts.append(f"__P{i}__\n{text}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to open profiles PDF: %s", exc)
        _write_json(CHARACTER_PROFILES_JSON, characters)
        return characters

    full = "\n".join(full_parts)
    headers = list(
        re.finditer(r"(?m)^(.{1,120}?)\s+BIOGRAPHY\s*$", full),
    )

    for hi, hm in enumerate(headers):
        raw_name = hm.group(1).strip()
        start = hm.end()
        end = headers[hi + 1].start() if hi + 1 < len(headers) else len(full)
        chunk = full[start:end]
        pm = re.search(r"__P(\d+)__", chunk)
        page_idx = int(pm.group(1)) - 1 if pm else 0
        try:
            ch = _parse_profile_chunk(raw_name, chunk, pdf_path, page_idx)
            characters.append(ch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed chunk for %s: %s", raw_name, exc)

    _write_json(CHARACTER_PROFILES_JSON, characters)
    return characters


def parse_character_sheets(pdf_path: Path) -> list[dict[str, Any]]:
    """
    Parse the all-character-sheets PDF into per-character rows.

    The published PDF is typically blank forms; when no filled names are
    found, returns an empty list. Writes ``character_sheets.json``.
    """

    rows: list[dict[str, Any]] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pi, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Sheets page %s extract_text: %s", pi + 1, exc)
                    continue
                for m in re.finditer(
                    r"(?mi)(?:CODENAME|NAME)\s*:\s*([^\n]+)",
                    text,
                ):
                    raw = m.group(1).strip()
                    if not _sheet_name_plausible(raw):
                        continue
                    rows.append(
                        {
                            "name": raw,
                            "powers": [],
                            "traits": [],
                            "rank": None,
                            "faction": "",
                            "source_page": pi + 1,
                        },
                    )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to parse character sheets: %s", exc)

    _write_json(CHARACTER_SHEETS_JSON, rows)
    return rows


def parse_errata(pdf_path: Path) -> list[dict[str, Any]]:
    """
    Parse the errata PDF into structured entries.

    Writes ``data/knowledge/errata.json``.
    """

    entries: list[dict[str, Any]] = []
    version = "20250718"

    try:
        with pdfplumber.open(pdf_path) as pdf:
            parts: list[str] = []
            for pi, page in enumerate(pdf.pages):
                try:
                    t = page.extract_text() or ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Errata page %s: %s", pi + 1, exc)
                    t = ""
                parts.append(t)
        full_text = "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to open errata PDF: %s", exc)
        _write_json(ERRATA_JSON, entries)
        return entries

    blocks = re.split(r"(?m)^(Page\s+[\d\-\s]+)\s*$", full_text)
    i = 1
    while i + 1 < len(blocks):
        page_ref = blocks[i].strip()
        body = blocks[i + 1].strip()
        i += 2
        lower = body.lower()
        idx = lower.find("it should read:")
        if idx == -1:
            idx = lower.find("should read:")
        if idx != -1:
            original_text = re.sub(r"\s+", " ", body[:idx].strip())
            corrected_text = re.sub(r"\s+", " ", body[idx:].split(":", 1)[-1].strip())
        else:
            original_text = re.sub(r"\s+", " ", body[:500])
            corrected_text = ""

        entries.append(
            {
                "page_reference": page_ref,
                "original_text": original_text,
                "corrected_text": corrected_text,
                "version": version,
            },
        )

    _write_json(ERRATA_JSON, entries)
    return entries



def _merge_profiles_and_sheets(
    profiles: list[dict[str, Any]],
    sheets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge sheet fields into profile rows by normalized name."""

    sheet_by: dict[str, dict[str, Any]] = {}
    for s in sheets:
        sheet_by[_normalize_key(s["name"])] = s

    merged: list[dict[str, Any]] = []
    for p in profiles:
        key = _normalize_key(p["name"])
        s = sheet_by.pop(key, None)
        row = dict(p)
        if s:
            if s.get("powers"):
                row["powers"] = s["powers"]
            if s.get("traits"):
                row["traits"] = s["traits"]
            if s.get("rank") is not None:
                row["rank"] = s["rank"]
            if s.get("faction"):
                row["faction"] = s["faction"]
        else:
            row.setdefault("powers", [])
            row.setdefault("traits", [])
        merged.append(row)

    for s in sheet_by.values():
        m = dict(s)
        m.setdefault("abilities", [])
        m.setdefault("stats", {})
        m.setdefault("description", "")
        merged.append(m)

    return merged


def _build_factions(characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect unique factions and member character names."""

    fac_members: dict[str, set[str]] = {}
    for c in characters:
        fac = (c.get("faction") or "").strip()
        if not fac:
            continue
        name = c.get("name") or ""
        for part in re.split(r"[,;]", fac):
            part = part.strip()
            if not part:
                continue
            fac_members.setdefault(part, set()).add(name)

    return [
        {"name": fn, "members": sorted(ms)}
        for fn, ms in sorted(fac_members.items(), key=lambda x: x[0].lower())
    ]


def build_knowledge_graph() -> dict[str, Any]:
    """
    Run all parsers, merge characters, write ``knowledge_base.json``,
    insert knowledge nodes, and log the scrape.
    """

    database.init_db()
    profiles_path = PDF_DIR / f"{_PROFILES_PDF_KEY}.pdf"
    sheets_path = PDF_DIR / f"{_SHEETS_PDF_KEY}.pdf"
    errata_path = PDF_DIR / f"{_ERRATA_PDF_KEY}.pdf"

    profiles: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []
    errata: list[dict[str, Any]] = []

    if profiles_path.is_file():
        profiles = parse_character_profiles(profiles_path)
    else:
        logger.warning("Missing profiles PDF: %s", profiles_path)

    if sheets_path.is_file():
        sheets = parse_character_sheets(sheets_path)
    else:
        logger.warning("Missing sheets PDF: %s", sheets_path)

    if errata_path.is_file():
        errata = parse_errata(errata_path)
    else:
        logger.warning("Missing errata PDF: %s", errata_path)

    characters = _merge_profiles_and_sheets(profiles, sheets)
    factions = _build_factions(characters)
    last_updated = _utc_iso()

    payload = {
        "characters": characters,
        "errata": errata,
        "factions": factions,
        "last_updated": last_updated,
    }
    _write_json(KNOWLEDGE_BASE_JSON, payload)

    nodes_created = 0
    for c in characters:
        name = str(c.get("name") or "Unknown")
        try:
            database.insert_knowledge_node(
                character_name=name,
                node_type="character",
                content=json.dumps(c, ensure_ascii=False),
                source_pdf=str(profiles_path),
                version_tag=last_updated,
            )
            nodes_created += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("insert_knowledge_node failed for %s: %s", name, exc)

    pdfs_ok = sum(
        1
        for p in (profiles_path, sheets_path, errata_path)
        if p.is_file()
    )
    success = bool(profiles_path.is_file())
    database.log_scrape(
        scraped_at=last_updated,
        pdfs_processed=pdfs_ok,
        nodes_created=nodes_created,
        success=success,
    )

    summary = {
        "nodes_created": nodes_created,
        "characters": len(characters),
        "factions": len(factions),
        "errata": len(errata),
    }
    print(
        f"Parsed {summary['characters']} characters, "
        f"{summary['factions']} factions, "
        f"{summary['errata']} errata entries",
    )
    return summary


def load_knowledge_base() -> dict[str, Any]:
    """
    Load ``knowledge_base.json`` from disk.

    If it does not exist (e.g. PDFs unavailable during a demo), fall back to
    ``demo/mock_knowledge.json`` so the dashboard and auditor can still run.
    """

    mock_path = Path(__file__).resolve().parent.parent / "demo" / "mock_knowledge.json"

    def _load_mock() -> dict[str, Any] | None:
        try:
            if mock_path.is_file():
                return json.loads(mock_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return None

    if KNOWLEDGE_BASE_JSON.is_file():
        kb = json.loads(KNOWLEDGE_BASE_JSON.read_text(encoding="utf-8"))
        # Demo reliability: supplement missing well-known characters from mock knowledge.
        mock = _load_mock()
        if mock:
            kb_chars = list(kb.get("characters", []) or [])
            kb_by = {str(c.get("name", "")).strip().casefold(): c for c in kb_chars if isinstance(c, dict)}
            for c in mock.get("characters", []) or []:
                if not isinstance(c, dict):
                    continue
                key = str(c.get("name", "")).strip().casefold()
                if key and key not in kb_by:
                    kb_chars.append(c)
            kb["characters"] = kb_chars

            kb_factions = list(kb.get("factions", []) or [])
            kb_fac_by = {str(f.get("name", "")).strip().casefold(): f for f in kb_factions if isinstance(f, dict)}
            for f in mock.get("factions", []) or []:
                if not isinstance(f, dict):
                    continue
                key = str(f.get("name", "")).strip().casefold()
                if key and key not in kb_fac_by:
                    kb_factions.append(f)
            kb["factions"] = kb_factions

        return kb

    mock = _load_mock()
    if mock:
        return mock

    raise FileNotFoundError(
        f"Knowledge base not found at {KNOWLEDGE_BASE_JSON} and demo fallback "
        f"not found at {mock_path}. Run the scraper (e.g. `python main.py --scrape-only`) "
        "or add demo/mock_knowledge.json.",
    )


def parse_pdf(path: Path) -> dict[str, Any]:
    """Detect PDF type by filename and run the appropriate parser."""

    name = path.name.lower()
    if "characterprofiles" in name or "character_profiles" in name:
        return {"characters": parse_character_profiles(path)}
    if "character_sheets" in name or "all_character" in name:
        return {"sheets": parse_character_sheets(path)}
    if "errata" in name:
        return {"errata": parse_errata(path)}
    return {}
