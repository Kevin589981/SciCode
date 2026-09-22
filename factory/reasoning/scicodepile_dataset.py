"""Prepare repository snapshots directly from SciCodePile's cleaned code data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path, PurePosixPath

from .discovery import (
    SCICODEPILE_DATASET,
    SCICODEPILE_REVISION,
    DiscoveryError,
    stratify_repositories,
    write_catalog,
)

DATASET_LICENSE = "apache-2.0"
INDEX_SCHEMA = "scicodepile-file-index-v1"
SNAPSHOT_SCHEMA = "scicodepile-clean-snapshot-v1"


def safe_slug(full_name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "--", full_name).strip("-.")
    return value[:160] or "repository"


def valid_full_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return None
    return value


def safe_relative_path(value: object) -> str | None:
    """Normalize a dataset file path and reject traversal/absolute paths."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip().replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return None
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    # The production workspace may be a shared filesystem where WAL locking is
    # unsupported.  A rollback journal is slower but portable and resumable.
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_files (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            indexed INTEGER NOT NULL DEFAULT 0,
            extracted_selection TEXT
        );
        CREATE TABLE IF NOT EXISTS files (
            full_name TEXT NOT NULL,
            path TEXT NOT NULL,
            language TEXT NOT NULL,
            size INTEGER NOT NULL,
            is_python INTEGER NOT NULL,
            is_readme INTEGER NOT NULL,
            PRIMARY KEY (full_name, path)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS keywords (
            full_name TEXT NOT NULL,
            keyword TEXT NOT NULL,
            PRIMARY KEY (full_name, keyword)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS extracted_files (
            selection TEXT NOT NULL,
            full_name TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            PRIMARY KEY (selection, full_name, path)
        ) WITHOUT ROWID;
        """
    )
    return connection


def _arrow_batches(
    path: Path,
    *,
    include_content: bool,
    invalid_rows: dict[str, int] | None = None,
):
    try:
        import pyarrow.csv as arrow_csv
    except ImportError as exc:
        raise DiscoveryError(
            "pyarrow is required; run this command in the uv SciCode environment"
        ) from exc
    columns = [
        "keyword",
        "repo_name",
        "file_path",
        "file_extension",
        "file_size",
        "language",
    ]
    if include_content:
        columns.append("content")

    def skip_invalid_row(_row) -> str:
        if invalid_rows is not None:
            key = str(path)
            invalid_rows[key] = invalid_rows.get(key, 0) + 1
        return "skip"

    reader = arrow_csv.open_csv(
        path,
        read_options=arrow_csv.ReadOptions(block_size=128 * 1024 * 1024),
        parse_options=arrow_csv.ParseOptions(
            newlines_in_values=True,
            invalid_row_handler=skip_invalid_row,
        ),
        convert_options=arrow_csv.ConvertOptions(include_columns=columns),
    )
    yield from reader


def index_dataset(csv_files: list[Path], connection: sqlite3.Connection) -> dict:
    """Index cleaned file metadata; completed CSVs are skipped on resume."""
    indexed_files = 0
    invalid_rows: dict[str, int] = {}
    for position, path in enumerate(csv_files, 1):
        size = path.stat().st_size
        previous = connection.execute(
            "SELECT size, indexed FROM source_files WHERE path = ?", (str(path),)
        ).fetchone()
        if previous == (size, 1):
            continue
        connection.execute(
            "INSERT INTO source_files(path, size, indexed) VALUES (?, ?, 0) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, indexed=0",
            (str(path), size),
        )
        rows_seen = 0
        for batch in _arrow_batches(
            path,
            include_content=False,
            invalid_rows=invalid_rows,
        ):
            values = batch.to_pydict()
            file_rows = []
            keyword_rows = []
            for repo, keyword, file_path, extension, file_size, language in zip(
                values["repo_name"],
                values["keyword"],
                values["file_path"],
                values["file_extension"],
                values["file_size"],
                values["language"],
            ):
                full_name = valid_full_name(repo)
                relative = safe_relative_path(file_path)
                if full_name is None or relative is None:
                    continue
                extension = str(extension or "").casefold()
                name = PurePosixPath(relative).name.casefold()
                file_rows.append(
                    (
                        full_name,
                        relative,
                        str(language or ""),
                        max(0, int(file_size or 0)),
                        int(extension == ".py" or str(language).casefold() == "python"),
                        int(name.startswith("readme")),
                    )
                )
                keyword = " ".join(str(keyword or "").split())[:120]
                if keyword:
                    keyword_rows.append((full_name, keyword))
            with connection:
                connection.executemany(
                    "INSERT OR IGNORE INTO files VALUES (?, ?, ?, ?, ?, ?)", file_rows
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO keywords VALUES (?, ?)", keyword_rows
                )
            rows_seen += batch.num_rows
        with connection:
            connection.execute(
                "UPDATE source_files SET indexed=1 WHERE path=?", (str(path),)
            )
        indexed_files += 1
        print(
            json.dumps(
                {
                    "event": "indexed",
                    "file": str(path),
                    "position": position,
                    "total": len(csv_files),
                    "rows": rows_seen,
                    "invalid_rows": invalid_rows.get(str(path), 0),
                }
            ),
            flush=True,
        )
    repository_count = connection.execute(
        "SELECT COUNT(DISTINCT full_name) FROM files"
    ).fetchone()[0]
    python_repository_count = connection.execute(
        "SELECT COUNT(DISTINCT full_name) FROM files WHERE is_python=1"
    ).fetchone()[0]
    return {
        "newly_indexed_files": indexed_files,
        "invalid_index_rows": sum(invalid_rows.values()),
        "invalid_index_rows_by_file": invalid_rows,
        "repositories": repository_count,
        "python_repositories": python_repository_count,
    }


def select_repositories(
    connection: sqlite3.Connection, *, limit: int
) -> tuple[list[dict], str]:
    if limit < 1:
        raise DiscoveryError("repository limit must be positive")
    keywords: dict[str, list[str]] = {}
    for full_name, keyword in connection.execute(
        "SELECT full_name, keyword FROM keywords ORDER BY keyword, full_name"
    ):
        keywords.setdefault(full_name, []).append(keyword)
    rows = []
    for full_name, files, source_bytes, readmes in connection.execute(
        "SELECT full_name, COUNT(*), SUM(size), SUM(is_readme) "
        "FROM files GROUP BY full_name HAVING SUM(is_python) > 0"
    ):
        rows.append(
            {
                "full_name": full_name,
                "matched_keywords": keywords.get(full_name, []),
                "dataset_files": int(files),
                "dataset_bytes": int(source_bytes or 0),
                "dataset_readmes": int(readmes or 0),
            }
        )
    selected = stratify_repositories(rows, limit=limit)
    canonical = "\n".join(sorted(row["full_name"].casefold() for row in selected))
    return selected, hashlib.sha256(canonical.encode()).hexdigest()


def _write_clean_file(path: Path, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    if path.exists():
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if current != digest:
            raise DiscoveryError(f"conflicting SciCodePile content for {path}")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_bytes(data)
    temporary.replace(path)
    return digest


def extract_snapshots(
    csv_files: list[Path],
    connection: sqlite3.Connection,
    selected: list[dict],
    *,
    selection: str,
    snapshots_root: Path,
) -> dict:
    """Reconstruct selected repositories from the already-cleaned file rows."""
    selected_names = {row["full_name"] for row in selected}
    snapshots_root.mkdir(parents=True, exist_ok=True)
    extracted_rows = 0
    invalid_rows: dict[str, int] = {}
    for position, path in enumerate(csv_files, 1):
        previous = connection.execute(
            "SELECT extracted_selection FROM source_files WHERE path=?", (str(path),)
        ).fetchone()
        if previous and previous[0] == selection:
            continue
        rows_seen = 0
        rows_written = 0
        for batch in _arrow_batches(
            path,
            include_content=True,
            invalid_rows=invalid_rows,
        ):
            values = batch.to_pydict()
            records = []
            for repo, file_path, content in zip(
                values["repo_name"], values["file_path"], values["content"]
            ):
                if repo not in selected_names:
                    continue
                relative = safe_relative_path(file_path)
                if relative is None or not isinstance(content, str):
                    continue
                destination = snapshots_root / safe_slug(repo) / Path(relative)
                data = content.encode("utf-8")
                digest = _write_clean_file(destination, data)
                records.append((selection, repo, relative, digest, len(data)))
                rows_written += 1
            with connection:
                connection.executemany(
                    "INSERT INTO extracted_files VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(selection, full_name, path) DO UPDATE SET "
                    "sha256=excluded.sha256, size=excluded.size",
                    records,
                )
            rows_seen += batch.num_rows
        with connection:
            connection.execute(
                "UPDATE source_files SET extracted_selection=? WHERE path=?",
                (selection, str(path)),
            )
        extracted_rows += rows_written
        print(
            json.dumps(
                {
                    "event": "extracted",
                    "file": str(path),
                    "position": position,
                    "total": len(csv_files),
                    "rows": rows_seen,
                    "selected_rows": rows_written,
                    "invalid_rows": invalid_rows.get(str(path), 0),
                }
            ),
            flush=True,
        )
    return {
        "newly_extracted_rows": extracted_rows,
        "invalid_extraction_rows": sum(invalid_rows.values()),
        "invalid_extraction_rows_by_file": invalid_rows,
    }


def build_catalog(
    connection: sqlite3.Connection,
    selected: list[dict],
    *,
    selection: str,
    snapshots_root: Path,
    dataset: str,
    revision: str,
) -> list[dict]:
    catalog = []
    for source in selected:
        full_name = source["full_name"]
        files = list(
            connection.execute(
                "SELECT path, sha256, size FROM extracted_files "
                "WHERE selection=? AND full_name=? ORDER BY path",
                (selection, full_name),
            )
        )
        if not files:
            continue
        digest = hashlib.sha256()
        for path, file_hash, size in files:
            digest.update(path.encode())
            digest.update(b"\0")
            digest.update(file_hash.encode())
            digest.update(b"\0")
            digest.update(str(size).encode())
            digest.update(b"\n")
        snapshot_hash = digest.hexdigest()
        snapshot_path = (snapshots_root / safe_slug(full_name)).resolve()
        metadata = {
            "schema_version": SNAPSHOT_SCHEMA,
            "full_name": full_name,
            "source_dataset": dataset,
            "source_revision": revision,
            "snapshot_hash": snapshot_hash,
            "files": len(files),
            "bytes": sum(row[2] for row in files),
            "matched_keywords": source["matched_keywords"],
        }
        (snapshot_path / ".scicodepile_snapshot.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        catalog.append(
            {
                "schema_version": "scicode-repository-candidate-v1",
                "repo_id": f"scicodepile:{full_name.casefold()}",
                "full_name": full_name,
                "url": f"https://github.com/{full_name}",
                "clone_url": "",
                "default_branch": "",
                "description": "",
                "topics": [],
                "language": "Python",
                "size_kb": (metadata["bytes"] + 1023) // 1024,
                "stars": 0,
                "forks": 0,
                "archived": False,
                "fork": False,
                "disabled": False,
                "license": "unknown",
                "matched_keywords": source["matched_keywords"],
                "discovery_channels": ["scicodepile_clean_dataset"],
                "source_kind": "scicodepile_clean_dataset",
                "source_dataset": dataset,
                "source_dataset_license": DATASET_LICENSE,
                "source_revision": revision,
                "snapshot_path": str(snapshot_path),
                "snapshot_hash": snapshot_hash,
                "dataset_files": metadata["files"],
                "dataset_bytes": metadata["bytes"],
            }
        )
    return catalog


def download_dataset(
    *, dataset: str, revision: str, raw_dir: Path, workers: int
) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise DiscoveryError(
            "huggingface_hub is required; run this command in the uv environment"
        ) from exc
    snapshot_download(
        repo_id=dataset,
        repo_type="dataset",
        revision=revision,
        allow_patterns=["data/*.csv", "README.md"],
        local_dir=raw_dir,
        max_workers=workers,
    )


def prepare_dataset(
    *,
    raw_dir: Path,
    prepared_root: Path,
    catalog_path: Path,
    index_path: Path,
    repository_limit: int,
    dataset: str = SCICODEPILE_DATASET,
    revision: str = SCICODEPILE_REVISION,
) -> dict:
    csv_files = sorted((raw_dir / "data").glob("*.csv"))
    if not csv_files:
        raise DiscoveryError(
            f"no downloaded SciCodePile CSV files under {raw_dir / 'data'}"
        )
    connection = _connect(index_path)
    try:
        stored = dict(connection.execute("SELECT key, value FROM metadata"))
        expected = {"schema": INDEX_SCHEMA, "dataset": dataset, "revision": revision}
        if stored and any(stored.get(key) != value for key, value in expected.items()):
            raise DiscoveryError(
                f"index {index_path} belongs to another dataset/revision"
            )
        with connection:
            connection.executemany(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                expected.items(),
            )
        index_report = index_dataset(csv_files, connection)
        selected, selection = select_repositories(connection, limit=repository_limit)
        snapshots_root = prepared_root / "snapshots"
        extraction_report = extract_snapshots(
            csv_files,
            connection,
            selected,
            selection=selection,
            snapshots_root=snapshots_root,
        )
        catalog = build_catalog(
            connection,
            selected,
            selection=selection,
            snapshots_root=snapshots_root,
            dataset=dataset,
            revision=revision,
        )
        write_catalog(catalog_path, catalog)
    finally:
        connection.close()
    report = {
        "schema_version": "scicodepile-preparation-report-v1",
        "dataset": dataset,
        "revision": revision,
        "raw_files": len(csv_files),
        "raw_bytes": sum(path.stat().st_size for path in csv_files),
        "selected_repositories": len(selected),
        "prepared_repositories": len(catalog),
        "selection_hash": selection,
        "catalog": str(catalog_path),
        **index_report,
        **extraction_report,
    }
    report_path = catalog_path.with_name(catalog_path.name + ".meta.json")
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--repository-limit", type=int, default=3600)
    parser.add_argument("--dataset", default=SCICODEPILE_DATASET)
    parser.add_argument("--revision", default=SCICODEPILE_REVISION)
    parser.add_argument("--download-workers", type=int, default=4)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()
    if not args.skip_download:
        download_dataset(
            dataset=args.dataset,
            revision=args.revision,
            raw_dir=args.raw_dir,
            workers=args.download_workers,
        )
    report = prepare_dataset(
        raw_dir=args.raw_dir.resolve(),
        prepared_root=args.prepared_root.resolve(),
        catalog_path=args.catalog.resolve(),
        index_path=(args.index or args.prepared_root / "index.sqlite3").resolve(),
        repository_limit=args.repository_limit,
        dataset=args.dataset,
        revision=args.revision,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
