from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import httpx
from dotenv import load_dotenv
from neo4j import GraphDatabase


DATASET_ROWS_URL = "https://datasets-server.huggingface.co/rows"
DATASET_NAME = "nlp-guild/medical-data"
DATASET_CONFIG = "default"
DATASET_SPLIT = "train"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import open Chinese medical dataset into Neo4j.")
    parser.add_argument("--cache", default="data/medical-data.jsonl", help="Local JSONL cache path.")
    parser.add_argument("--page-size", type=int, default=100, help="Hugging Face rows page size.")
    parser.add_argument("--batch-size", type=int, default=200, help="Neo4j import batch size.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max row count, 0 means all rows.")
    parser.add_argument("--skip-download", action="store_true", help="Use existing cache only.")
    parser.add_argument("--download-only", action="store_true", help="Only download/cache the dataset; do not import.")
    parser.add_argument("--resume", action="store_true", help="Resume appending from existing cache line count.")
    args = parser.parse_args()

    load_dotenv()
    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.skip_download:
        total = download_dataset(cache_path, args.page_size, args.limit, args.resume)
        print(f"Downloaded {total} rows to {cache_path}")
    if args.download_only:
        return

    rows = list(read_jsonl(cache_path))
    print(f"Loaded {len(rows)} cached rows")
    stats = import_to_neo4j(rows, args.batch_size)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def download_dataset(cache_path: Path, page_size: int, limit: int, resume: bool = False) -> int:
    total = count_lines(cache_path) if resume and cache_path.exists() else 0
    mode = "a" if resume and cache_path.exists() else "w"
    with httpx.Client(timeout=120, trust_env=False) as client, cache_path.open(mode, encoding="utf-8") as out:
        while True:
            length = page_size if not limit else min(page_size, limit - total)
            if length <= 0:
                break
            response = get_rows_page(client, total, length)
            payload = response.json()
            rows = payload.get("rows", [])
            if not rows:
                break
            for row in rows:
                item = row.get("row", {})
                out.write(json.dumps(item, ensure_ascii=False) + "\n")
            total += len(rows)
            print(f"downloaded {total}")
            if limit and total >= limit:
                break
            if len(rows) < length:
                break
    return total


def get_rows_page(client: httpx.Client, offset: int, length: int) -> httpx.Response:
    params = {
        "dataset": DATASET_NAME,
        "config": DATASET_CONFIG,
        "split": DATASET_SPLIT,
        "offset": offset,
        "length": length,
    }
    for attempt in range(1, 6):
        try:
            response = client.get(DATASET_ROWS_URL, params=params)
            if response.status_code == 429:
                wait_seconds = 15 * attempt
                print(f"rate limited at offset {offset}; waiting {wait_seconds}s")
                time.sleep(wait_seconds)
                continue
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            wait_seconds = 10 * attempt
            print(f"request failed at offset {offset}: {exc}; waiting {wait_seconds}s")
            time.sleep(wait_seconds)
    response = client.get(DATASET_ROWS_URL, params=params)
    response.raise_for_status()
    return response


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def import_to_neo4j(rows: List[Dict[str, Any]], batch_size: int) -> Dict[str, int]:
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    timeout = float(os.getenv("NEO4J_TIMEOUT", "5"))

    driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=timeout)
    stats = {
        "disease_rows": 0,
        "symptom_links": 0,
        "common_drug_links": 0,
        "recommended_drug_links": 0,
        "department_links": 0,
    }
    try:
        with driver.session() as session:
            session.execute_write(create_constraints)
            normalized_rows = [normalize_row(row) for row in rows]
            normalized_rows = [row for row in normalized_rows if row["disease"]]
            for batch in chunked(normalized_rows, batch_size):
                session.execute_write(upsert_medical_batch, batch)
                for row in batch:
                    stats["disease_rows"] += 1
                    stats["symptom_links"] += len(row["symptoms"])
                    stats["common_drug_links"] += len(row["common_drugs"])
                    stats["recommended_drug_links"] += len(row["recommended_drugs"])
                    stats["department_links"] += len(row["departments"])
    finally:
        driver.close()
    return stats


def create_constraints(tx) -> None:
    tx.run("CREATE CONSTRAINT disease_name IF NOT EXISTS FOR (d:Disease) REQUIRE d.name IS UNIQUE")
    tx.run("CREATE CONSTRAINT symptom_name IF NOT EXISTS FOR (s:Symptom) REQUIRE s.name IS UNIQUE")
    tx.run("CREATE CONSTRAINT medicine_name IF NOT EXISTS FOR (m:Medicine) REQUIRE m.name IS UNIQUE")
    tx.run("CREATE CONSTRAINT department_name IF NOT EXISTS FOR (d:Department) REQUIRE d.name IS UNIQUE")


def upsert_medical_batch(tx, rows: List[Dict[str, Any]]) -> None:
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (d:Disease {name: row.disease})
        SET d.description = coalesce(row.description, d.description),
            d.source = row.source,
            d.prevent = row.prevent,
            d.cause = row.cause,
            d.cure_lasttime = row.cure_lasttime,
            d.cured_prob = row.cured_prob,
            d.get_way = row.get_way
        FOREACH (name IN row.symptoms |
            MERGE (s:Symptom {name: name})
            MERGE (d)-[:HAS_SYMPTOM]->(s)
        )
        FOREACH (name IN row.common_drugs |
            MERGE (m:Medicine {name: name})
            MERGE (d)-[:TREATED_BY]->(m)
        )
        FOREACH (name IN row.recommended_drugs |
            MERGE (m:Medicine {name: name})
            MERGE (d)-[:RECOMMENDED_DRUG]->(m)
        )
        FOREACH (name IN row.departments |
            MERGE (dept:Department {name: name})
            MERGE (d)-[:VISIT_DEPARTMENT]->(dept)
        )
        """,
        rows=rows,
    )


def chunked(values: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "disease": clean_text(row.get("name")),
        "description": clean_text(row.get("desc")),
        "prevent": clean_text(row.get("prevent")),
        "cause": clean_text(row.get("cause")),
        "cure_lasttime": clean_text(row.get("cure_lasttime")),
        "cured_prob": clean_text(row.get("cured_prob")),
        "get_way": clean_text(row.get("get_way")),
        "symptoms": clean_list(row.get("symptom")),
        "departments": clean_departments(row.get("cure_department") or row.get("category")),
        "common_drugs": clean_list(row.get("common_drug")),
        "recommended_drugs": clean_list(row.get("recommand_drug")),
        "source": DATASET_NAME,
    }


def clean_departments(value: Any) -> List[str]:
    items = clean_list(value)
    return [item for item in items if item not in {"疾病百科"}]


def clean_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = clean_text(item)
        if text and text not in result:
            result.append(text)
    return result


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


if __name__ == "__main__":
    main()
