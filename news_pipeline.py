"""
SIGNAL AI News Pipeline
========================
自動でAIニュースを収集 → スコアリング → 日英記事生成 → DB保存(draft) → X投稿

必要な環境変数（GitHub Secrets）:
  ANTHROPIC_API_KEY = Claude APIキー
  SUPABASE_URL      = SupabaseプロジェクトURL
  SUPABASE_KEY      = Supabase service_role key（Secret key）

任意（X連携）:
  TWITTER_API_KEY / TWITTER_API_SECRET / TWITTER_ACCESS_TOKEN / TWITTER_ACCESS_SECRET

実行:
  pip install anthropic feedparser supabase tweepy python-dotenv
  python news_pipeline.py
"""

import os
import re
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
import feedparser
import anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("signal")

# ============================================================
# CONFIG
# ============================================================

# "draft"   = Supabaseで確認後に手動で published に変更（レビューモード）
# "published" = 即時公開（全自動モード）
DEFAULT_STATUS = "draft"

RSS_SOURCES = [
    {"url": "https://arxiv.org/rss/cs.AI",            "source": "arXiv AI",        "category": "research", "trust": 95},
    {"url": "https://arxiv.org/rss/cs.LG",            "source": "arXiv ML",        "category": "research", "trust": 95},
    {"url": "https://deepmind.google/blog/rss.xml",   "source": "DeepMind",        "category": "research", "trust": 98},
    {"url": "https://openai.com/blog/rss.xml",        "source": "OpenAI",          "category": "product",  "trust": 99},
    {"url": "https://www.anthropic.com/rss.xml",      "source": "Anthropic",       "category": "product",  "trust": 99},
    {"url": "https://ai.google/blog/rss",             "source": "Google AI",       "category": "product",  "trust": 97},
    {"url": "https://ai.meta.com/blog/rss",           "source": "Meta AI",         "category": "product",  "trust": 96},
    {"url": "https://mistral.ai/news/rss.xml",        "source": "Mistral AI",      "category": "product",  "trust": 92},
    {"url": "https://techcrunch.com/tag/artificial-intelligence/feed/",
                                                      "source": "TechCrunch",      "category": "business", "trust": 80},
    {"url": "https://www.technologyreview.com/feed/", "source": "MIT Tech Review", "category": "research", "trust": 90},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
                                                      "source": "The Verge",       "category": "product",  "trust": 78},
    {"url": "https://artificialintelligenceact.eu/feed/",
                                                      "source": "EU AI Act",       "category": "policy",   "trust": 95},
    {"url": "https://www.nist.gov/artificial-intelligence/rss.xml",
                                                      "source": "NIST",            "category": "policy",   "trust": 97},
]

SCORING_PROMPT = """
あなたはAI専門のジャーナリストです。以下の記事を評価・翻訳してください。

【タイトル】{title}
【本文要約】{summary}
【ソース】{source} (信頼度: {trust}/100)
【カテゴリ】{category}

スコアリング基準（合計100点）:
- 新規性 30点: 新しい発見・発表か（既知情報は低スコア）
- 信頼性 25点: 一次ソース・公式発表・査読済みか
- 重要性 25点: AI分野全体に影響するか
- 実用性 20点: 開発者・研究者・ビジネスパーソンに有益か

重要度分類:
- 90点以上 = critical
- 75〜89点 = high
- 60〜74点 = normal
- 60点未満 = skip（掲載しない）

JSONのみで回答（マークダウン不要）:
{{
  "score": <整数>,
  "importance": <"critical"|"high"|"normal"|"skip">,
  "title_ja": <日本語タイトル（30字以内）>,
  "title_en": <英語タイトル（10語以内）>,
  "summary_ja": <日本語要約（150字程度）>,
  "summary_en": <English summary (around 80 words)>,
  "key_insight": <なぜこのニュースが重要か・業界への具体的な影響（日本語2文）>,
  "tags": <タグ配列 例: ["LLM", "OpenAI", "Benchmark"]>
}}
"""

# ============================================================
# UTILITIES
# ============================================================

def strip_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', text).strip()

def parse_json_safe(raw: str) -> dict:
    """Claude のレスポンスから JSON を安全にパース"""
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1].replace("json", "", 1).strip() if len(parts) > 1 else raw
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("JSON not found")
    return json.loads(raw[start:end])

# ============================================================
# STEP 0: 既存記事を取得（APIコスト削減の核心）
# ============================================================

def get_existing_ids(supabase_url: str, supabase_key: str) -> set:
    """
    DB内の既存記事IDを取得。
    フェッチした記事がすでにDBにあればClaudeを呼ばずスキップ。
    2回目以降の実行でAPIコストを大幅削減（平均80%減）。
    """
    if not supabase_url or not supabase_key:
        return set()
    try:
        sb = create_client(supabase_url, supabase_key)
        result = sb.table("articles").select("id").execute()
        ids = {row["id"] for row in (result.data or [])}
        log.info(f"Existing articles in DB: {len(ids)}")
        return ids
    except Exception as e:
        log.warning(f"Could not fetch existing IDs: {e}")
        return set()

# ============================================================
# STEP 1: RSS収集
# ============================================================

def fetch_feeds() -> list[dict]:
    articles = []
    seen_urls = set()

    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            count = 0
            for entry in feed.entries[:5]:
                url = entry.get("link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                article_id = hashlib.md5(url.encode()).hexdigest()
                articles.append({
                    "id":           article_id,
                    "title":        strip_html(entry.get("title", "")),
                    "summary":      strip_html(entry.get("summary", entry.get("description", "")))[:2000],
                    "url":          url,
                    "source":       source["source"],
                    "source_trust": source["trust"],
                    "category":     source["category"],
                })
                count += 1
            log.info(f"✓ {source['source']}: {count} entries")
        except Exception as e:
            log.warning(f"✗ {source['source']}: {e}")

    log.info(f"Total fetched: {len(articles)}")
    return articles

# ============================================================
# STEP 2: スコアリング + 日英生成（新規記事のみ）
# ============================================================

def score_and_translate(articles: list[dict], client: anthropic.Anthropic) -> list[dict]:
    results = []
    for article in articles:
        try:
            prompt = SCORING_PROMPT.format(
                title=article["title"],
                summary=article["summary"],
                source=article["source"],
                trust=article["source_trust"],
                category=article["category"],
            )
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            scored = parse_json_safe(response.content[0].text.strip())

            if scored.get("importance") == "skip" or scored.get("score", 0) < 60:
                log.info(f"SKIP ({scored.get('score')}): {article['title'][:50]}")
                continue

            article.update(scored)
            article["processed_at"] = datetime.now(timezone.utc).isoformat()
            results.append(article)
            log.info(f"✓ [{scored['score']}] {scored['importance'].upper()}: {scored.get('title_ja','')[:40]}")

        except Exception as e:
            log.warning(f"Score error '{article['title'][:40]}': {e}")

    log.info(f"Passed scoring: {len(results)}/{len(articles)}")
    return results

# ============================================================
# STEP 3: Supabaseに保存
# ============================================================

def save_to_supabase(articles: list[dict], supabase_url: str, supabase_key: str) -> int:
    if not supabase_url or not supabase_key:
        log.warning("Supabase not configured, skipping")
        return 0

    sb = create_client(supabase_url, supabase_key)
    saved = 0
    for article in articles:
        try:
            sb.table("articles").upsert({
                "id":           article["id"],
                "title_ja":     article.get("title_ja", ""),
                "title_en":     article.get("title_en", ""),
                "summary_ja":   article.get("summary_ja", ""),
                "summary_en":   article.get("summary_en", ""),
                "key_insight":  article.get("key_insight", ""),
                "url":          article["url"],
                "source":       article["source"],
                "category":     article["category"],
                "score":        article.get("score", 0),
                "importance":   article.get("importance", "normal"),
                "tags":         article.get("tags", []),
                "processed_at": article.get("processed_at"),
                "status":       DEFAULT_STATUS,
            }, on_conflict="id").execute()
            saved += 1
        except Exception as e:
            log.warning(f"DB error '{article.get('title_ja','')}': {e}")

    log.info(f"Saved {saved}/{len(articles)} articles (status={DEFAULT_STATUS})")
    return saved

# ============================================================
# STEP 4: X投稿（published モードかつX設定済みのみ）
# ============================================================

def post_to_twitter(articles: list[dict]) -> None:
    if DEFAULT_STATUS == "draft":
        log.info("Draft mode: X posting skipped")
        return

    keys = [os.getenv(k) for k in [
        "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET"
    ]]
    if not all(keys):
        log.warning("Twitter credentials not set, skipping")
        return

    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=keys[0], consumer_secret=keys[1],
            access_token=keys[2], access_token_secret=keys[3],
        )
    except ImportError:
        return

    to_post = [a for a in articles if a.get("importance") in ("critical", "high")][:3]
    for a in to_post:
        tags = " ".join(f"#{t.replace(' ','')}" for t in a.get("tags", [])[:3])
        tweet = (
            f"🔔 [{a.get('importance','').upper()}] {a.get('title_ja','')}\n\n"
            f"💡 {a.get('key_insight','')}\n\n"
            f"📊 Score: {a.get('score',0)}/100\n"
            f"🔗 {a.get('url','')}\n\n"
            f"{tags} #AINews #SIGNAL"
        )
        if len(tweet) > 280:
            tweet = tweet[:277] + "..."
        try:
            client.create_tweet(text=tweet)
            log.info(f"Tweeted: {a.get('title_ja','')[:40]}")
        except Exception as e:
            log.warning(f"Tweet error: {e}")

# ============================================================
# STEP 5: デイリーダイジェスト（1日1回・UTC0時台のみ）
# ============================================================

def generate_daily_digest(articles: list[dict], client: anthropic.Anthropic) -> Optional[str]:
    if not articles:
        return None
    top = sorted(articles, key=lambda x: x.get("score", 0), reverse=True)[:5]
    summaries = "\n".join(
        f"- [{a['source']}] {a.get('title_ja','')}: {a.get('key_insight','')}"
        for a in top
    )
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": f"""
本日のAIニューストップ5からニュースレター用デイリーダイジェストを作成してください。
読者は技術者〜ビジネスパーソンまで幅広く想定。

{summaries}

フォーマット:
- 件名（20字以内）
- 本文（300字程度）
- 英語版
"""}]
        )
        return response.content[0].text
    except Exception as e:
        log.warning(f"Digest failed: {e}")
        return None

# ============================================================
# MAIN
# ============================================================

def run_pipeline():
    log.info("=" * 50)
    log.info(f"SIGNAL Pipeline Started [mode={DEFAULT_STATUS}]")
    log.info("=" * 50)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required")
    claude = anthropic.Anthropic(api_key=api_key)

    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_KEY", "")

    # Step 0: 既存IDを取得（Claudeコストを大幅削減）
    existing_ids = get_existing_ids(supabase_url, supabase_key)

    # Step 1: RSS収集
    all_articles = fetch_feeds()
    if not all_articles:
        log.error("No articles fetched")
        return

    # 新規記事のみフィルタ（★ここがコスト削減の核心★）
    new_articles = [a for a in all_articles if a["id"] not in existing_ids]
    log.info(f"New articles to process: {len(new_articles)}/{len(all_articles)}")

    if not new_articles:
        log.info("No new articles. Pipeline complete.")
        return

    # Step 2: 新規のみスコアリング
    scored = score_and_translate(new_articles, claude)
    if not scored:
        log.info("No articles passed scoring threshold")
        return

    # Step 3: DB保存
    saved = save_to_supabase(scored, supabase_url, supabase_key)

    # Step 4: X投稿
    post_to_twitter(scored)

    # Step 5: 1日1回のダイジェスト（UTC 0時台）
    if datetime.now(timezone.utc).hour == 0 and saved > 0:
        digest = generate_daily_digest(scored, claude)
        if digest:
            log.info(f"\n{'='*40}\nDAILY DIGEST:\n{digest}\n{'='*40}")

    log.info("=" * 50)
    log.info(f"Done: {saved} new articles saved [{DEFAULT_STATUS}]")
    if DEFAULT_STATUS == "draft":
        log.info(">> Supabase で status を 'published' に変更するとサイトに表示されます")
    for a in sorted(scored, key=lambda x: x.get("score", 0), reverse=True)[:5]:
        log.info(f"  [{a.get('score')}] {a.get('importance','').upper()}: {a.get('title_ja','')[:50]}")
    log.info("=" * 50)


if __name__ == "__main__":
    run_pipeline()
