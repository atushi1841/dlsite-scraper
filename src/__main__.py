"""
DLsite Scraper — Apify Actor entry point.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from scraper import (
    scrape_search,
    scrape_ranking,
    scrape_product_ids,
)


def main() -> None:
    """Apify Actor main entry point."""
    try:
        from apify import Actor
        import asyncio
    except ImportError:
        # Local CLI mode
        import argparse
        parser = argparse.ArgumentParser(description="DLsite Scraper")
        parser.add_argument("--mode", default="search", choices=["search", "ranking", "product_ids"])
        parser.add_argument("--keyword", default="ASMR")
        parser.add_argument("--ranking", default="daily")
        parser.add_argument("--work-type", default="all")
        parser.add_argument("--ids", default="")
        parser.add_argument("--max", type=int, default=10)
        parser.add_argument("--output", default="dlsite_results.json")
        args = parser.parse_args()

        if args.mode == "search":
            items = scrape_search(args.keyword, args.work_type, args.max)
        elif args.mode == "ranking":
            items = scrape_ranking(args.ranking, args.work_type, args.max)
        elif args.mode == "product_ids":
            ids = [i.strip() for i in args.ids.split(",") if i.strip()]
            items = scrape_product_ids(ids)
        else:
            items = []

        import json
        output = [i.to_dict() for i in items]
        path = Path(args.output)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        print(f"Saved {len(output)} items to {path}")
        return

    # Apify mode
    async def run_actor():
        async with Actor:
            actor_input = await Actor.get_input() or {}
            mode = actor_input.get("searchMode", "search")
            keyword = actor_input.get("searchKeyword", "ASMR")
            ranking_type = actor_input.get("rankingType", "daily")
            work_type = actor_input.get("workType", "all")
            product_ids_str = actor_input.get("productIds", "")
            max_results = int(actor_input.get("maxResults", 50))
            if max_results < 1:
                max_results = 1

            Actor.log.info(f"Mode: {mode}, max_results: {max_results}")

            if mode == "search":
                keyword = keyword or "ASMR"
                Actor.log.info(f"Searching: '{keyword}' (type: {work_type})")
                items = scrape_search(keyword, work_type, max_results)
            elif mode == "ranking":
                Actor.log.info(f"Ranking: {ranking_type} (type: {work_type})")
                items = scrape_ranking(ranking_type, work_type, max_results)
            elif mode == "product_ids":
                ids = [i.strip() for i in product_ids_str.split(",") if i.strip()]
                Actor.log.info(f"Product IDs: {ids[:5]}... ({len(ids)} total)")
                items = scrape_product_ids(ids)
            else:
                items = []

            Actor.log.info(f"Scraped {len(items)} items")
            for item in items:
                await Actor.push_data(item.to_dict())
            Actor.log.info(f"Pushed {len(items)} items to dataset")

    asyncio.run(run_actor())


if __name__ == "__main__":
    main()
