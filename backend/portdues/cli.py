"""Command line entry points, handy for running things without the web app.

    portdues ingest "Port Tariff.pdf"
    portdues ingest --compile-only doc_123
    portdues ask doc_123 "What are the port dues for ..."
    portdues eval doc_123 --limit 5
    portdues check-rules doc_123
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from portdues.storage.db import get_db, init_db


async def _ingest(args: argparse.Namespace) -> None:
    from portdues.documents import pdf_path, register_pdf
    from portdues.ingestion.pipeline import ingest

    db = get_db()
    if args.compile_only:
        doc = await db.get_document(args.target)
        await ingest(doc["id"], pdf_path(doc), compile_only=True, rule_ids=args.rules)
    else:
        path = Path(args.target)
        doc, created = await register_pdf(path.read_bytes(), path.name, args.name)
        if not created and doc["status"] == "ready" and not args.force:
            print(f"{doc['id']} already ingested (use --force to redo)")
            return
        print(f"ingesting as {doc['id']}")
        await ingest(doc["id"], pdf_path(doc), fresh=args.fresh)
    doc = await db.get_document(doc["id"])
    print(json.dumps({k: doc[k] for k in ("id", "status", "error", "stats")}, indent=2, default=str))


async def _ask(args: argparse.Namespace) -> None:
    from portdues.agents.strategies import run_turn

    async for event in run_turn(args.doc_id, args.question):
        if event["type"] == "token":
            print(event["text"], end="", flush=True)
        elif event["type"] == "profile":
            print(f"\n[profile] {json.dumps({k: v for k, v in event['profile'].items() if v not in (None, [])})}",
                  file=sys.stderr)
        elif event["type"] == "result":
            print("\n\nlines:", json.dumps([(l["name"], l["amount"], l["status"]) for l in event["lines"]], indent=1))


async def _eval(args: argparse.Namespace) -> None:
    from portdues.evals.runner import run_experiment

    rulebook = await get_db().get_rulebook(args.doc_id) or {}
    exp = await get_db().create_experiment(args.doc_id, args.name or "cli run", {
        "strategies": args.strategies, "scorers": args.scorers, "limit": args.limit,
        "difficulty": args.difficulty, "rulebook": rulebook.get("compiled_with", {}),
    })
    async for event in run_experiment(exp["id"]):
        if event["type"] in ("case", "done"):
            print(json.dumps(event, default=str)[:400])


async def _check_rules(args: argparse.Namespace) -> None:
    from portdues.evals.rulebook_check import check_rulebook
    from portdues.rules.schema import Rulebook

    body = await get_db().get_rulebook(args.doc_id)
    if body is None:
        sys.exit("no rulebook for this document")
    rulebook = Rulebook.model_validate(body)
    result = check_rulebook(rulebook)
    print(json.dumps({"compiled_with": rulebook.compiled_with, **{k: v for k, v in result.items() if k != "misses"}},
                     indent=1, default=str))
    for m in result["misses"]:
        print(f"  miss {m['case_id']:4} {m['charge']:18} expected {m['expected']:>12,.2f}  got {m['predicted']}")


async def _compare_rules(args: argparse.Namespace) -> None:
    from collections import Counter

    from portdues.evals.rulebook_check import compare_rulebooks
    from portdues.rules.schema import Rulebook

    candidate = Rulebook.model_validate(await get_db().get_rulebook(args.doc_id))
    reference = Rulebook.model_validate(json.loads(Path(args.reference).read_text()))
    result = compare_rulebooks(candidate, reference)
    print(json.dumps({k: v for k, v in result.items() if k != "disagreements"}, indent=1))
    by_charge = Counter(d["charge"] for d in result["disagreements"])
    for charge, n in by_charge.most_common():
        examples = [d for d in result["disagreements"] if d["charge"] == charge][:3]
        print(f"  {charge}: {n} disagreements, e.g. " + "; ".join(
            f"{d['call']}: {d['candidate']} vs {d['reference']}" for d in examples))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="portdues")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest")
    p.add_argument("target", help="PDF path, or a document id with --compile-only")
    p.add_argument("--name")
    p.add_argument("--force", action="store_true", help="re-run even if the document is ready")
    p.add_argument("--fresh", action="store_true", help="ignore cached page transcriptions and facts")
    p.add_argument("--compile-only", action="store_true")
    p.add_argument("--rules", nargs="*", help="with --compile-only: recompile just these rule ids")

    p = sub.add_parser("ask")
    p.add_argument("doc_id")
    p.add_argument("question")

    p = sub.add_parser("eval")
    p.add_argument("doc_id")
    p.add_argument("--name")
    p.add_argument("--strategies", nargs="+", default=["compiled"], choices=["compiled"])
    p.add_argument("--scorers", nargs="+", default=["numeric", "judge", "laya"])
    p.add_argument("--difficulty", nargs="*", default=None)
    p.add_argument("--limit", type=int, default=None)

    p = sub.add_parser("check-rules", help="score the compiled rulebook on the golden calls, no LLM calls")
    p.add_argument("doc_id")

    p = sub.add_parser("compare-rules", help="price a grid of calls with this rulebook and a reference one")
    p.add_argument("doc_id")
    p.add_argument("reference", help="path to a rulebook JSON, e.g. one compiled by another model")

    args = parser.parse_args()
    handler = {"ingest": _ingest, "ask": _ask, "eval": _eval, "check-rules": _check_rules,
               "compare-rules": _compare_rules}[args.cmd]

    async def run() -> None:
        db = await init_db()
        try:
            await handler(args)
        finally:
            await db.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
