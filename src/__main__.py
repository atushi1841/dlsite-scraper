"""
DLsite Scraper — Apify Actor entry point.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scraper import scrape_search, scrape_ranking, scrape_product_ids


def main() -> None:
    try:
        from apify import Actor
        import asyncio
    except ImportError:
        import argparse
        import json

        parser = argparse.ArgumentParser(description="DLsite Scraper")
        parser.add_argument("--mode", default="ranking", choices=["search", "ranking", "product_ids"])
        parser.add_argument("--keyword", default="ASMR")
        parser.add_argument("--ranking", default="weekly")
        parser.add_argument("--ids", default="")
        parser.add_argument("--max", type=int, default=10)
        parser.add_argument("--output", default="dlsite_results.json")
        args = parser.parse_args()

        items = []
        if args.mode == "search":
            items = scrape_search(args.keyword, args.max)
        elif args.mode == "ranking":
            items = scrape_ranking(args.ranking, args.max)
        elif args.mode == "product_ids":
            ids = [i.strip() for i in args.ids.split(",") if i.strip()]
            items = scrape_product_ids(ids)

        output = [i.to_dict() for i in items]
        Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2))
        print(f"Saved {len(output)} items to {args.output}")
        return

    async def run_actor():
        async with Actor:
            inp = await Actor.get_input() or {}
            mode = inp.get("searchMode", "ranking")
            keyword = inp.get("searchKeyword", "ASMR")
            ranking_type = inp.get("rankingType", "weekly")
            product_ids_str = inp.get("productIds", "")
            max_results = max(1, int(inp.get("maxResults", 50)))

            Actor.log.info(f"Mode={mode} max={max_results}")

            if mode == "search":
                items = scrape_search(keyword, max_results)
            elif mode == "ranking":
                items = scrape_ranking(ranking_type, max_results)
            elif mode == "product_ids":
                ids = [i.strip() for i in product_ids_str.split(",") if i.strip()]
                items = scrape_product_ids(ids)
            else:
                items = []

            Actor.log.info(f"Scraped {len(items)} items")
            for item in items:
                await Actor.push_data(item.to_dict())

    asyncio.run(run_actor())


if __name__ == "__main__":
    main()
