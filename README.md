# DLsite Scraper — 同人マーケットデータ抽出

Scrape [DLsite](https://www.dlsite.com) — Japan's largest doujin (同人) digital marketplace. Extract product data from voice/ASMR, games, manga, music, and software categories.

## Features

- **Keyword Search** — Search by title or keyword
- **Ranking Scraping** — Daily, weekly, monthly, and all-time rankings
- **Product ID Mode** — Scrape specific RJ/VJ/BJ IDs directly
- **Rich Data** — 25+ fields per product including:
  - Title, circle/brand name, work ID (RJ/VJ/BJ)
  - Price (sale + original), release date
  - Voice actors, genres, age rating
  - Work type, file format, file size
  - Sample images, description
  - Download count, rating, wishlist count, review count
- **Adult Content** — Full support for both all-ages and adult (R18) products

## Input

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| searchMode | enum | `search` | `search`, `ranking`, or `product_ids` |
| searchKeyword | string | `ASMR` | Keyword for search mode |
| rankingType | enum | `daily` | `daily`, `weekly`, `monthly`, or `total` |
| workType | enum | `all` | `all`, `voice`, `game`, `manga`, `music`, or `soft` |
| productIds | string | — | Comma-separated RJ/VJ/BJ IDs |
| maxResults | int | 50 | Max items (1–200) |

## Output

Each dataset item contains the full product data as JSON. Example:

```json
{
  "work_id": "RJ01678178",
  "title": "【10%OFF】【ブルーアーカイブ】ミヨASMR～先生が、悪いんですよ?～ [Yostar]",
  "circle": "Yostar",
  "price_jpy": 1188,
  "original_price_jpy": 1320,
  "release_date": "2026年07月27日 0時",
  "voice_actor": "花岩香奈",
  "age_rating": "全年齢",
  "work_type": "ボイス・ASMR",
  "file_size": "1.12GB",
  "genres": ["ASMR", "癒し", "バイノーラル/ダミヘ", "萌え"],
  "rating": 4.5,
  "dl_count": 12500,
  "image_url": "https://img.dlsite.jp/...",
  "url": "https://www.dlsite.com/home/work/=/product_id/RJ01678178.html"
}
```

## About DLsite

DLsite is Japan's largest marketplace for doujin (independent/self-published) digital content with over 600,000 products. Categories include:
- **Voice / ASMR** — The fastest-growing category with binaural audio, roleplay, and relaxation content
- **Games** — Indie games, RPGs, adventure, simulation
- **Manga / Doujinshi** — Self-published comics and illustrations
- **Music** — Independent music albums and tracks
- **Software** — Utilities, wallpapers, and tools

## Tech

Built with Python, Playwright (JS rendering for search), and httpx (direct HTTP for rankings and product details).

## License

MIT
