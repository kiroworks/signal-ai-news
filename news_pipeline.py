"""
SIGNAL AI News Pipeline
========================
自動でAIニュースを収集 → スコアリング → 日英記事生成 → DB保存 → X投稿

必要な環境変数:
  ANTHROPIC_API_KEY    = Claude APIキー
  SUPABASE_URL         = SupabaseプロジェクトURL
  SUPABASE_KEY         = Supabase anon key
  TWITTER_BEARER       = X(Twitter) Bearer Token
  TWITTER_API_KEY      = X API Key
  TWITTER_API_SECRET   = X API Secret
  TWITTER_ACCESS_TOKEN = X Access Token
  TWITTER_ACCESS_SECRET= X Access Token Secret

実行:
  pip install anthropic feedparser supabase tweepy python-dotenv
  python news_pipeline.py
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
import feedparser
import anthropic
from supabase import create_client
import tweepy
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("signal")

# ============================================================
# CONFIG
# ============================================================

# 厳選ソース一覧（信頼性・専門性の高いものだけ）
RSS_SOURCES = [
    # 研究・論文
    {"url": "https://arxiv.org/rss/cs.AI",          "source": "arXiv AI",         "category": "research", "trust": 95},
    {"url": "https://arxiv.org/rss/cs.LG",          "source": "arXiv ML",         "category": "research", "trust": 95},
    {"url": "https://deepmind.google/blog/rss.xml", "source": "DeepMind",         "category": "research", "trust": 98},

    # 公式ブログ
    {"url": "https://openai.com/blog/rss.xml",      "source": "OpenAI",           "category": "product",  "trust": 99},
    {"url": "https://www.anthropic.com/rss.xml",    "source": "Anthropic",        "category": "product",  "trust": 99},
    {"url": "https://ai.google/blog/rss",           "source": "Google AI",        "category": "product",  "trust": 97},
    {"url": "https://ai.meta.com/blog/rss",         "source": "Meta AI",          "category": "product",  "trust": 96},
    {"url": "https://mistral.ai/news/rss.xml",      "source": "Mistral AI",       "category": "product",  "trust": 92},

    # テックメディア（高品質のもののみ）
    {"url": "https://techcrunch.com/tag/artificial-intelligence/feed/",
                                                    "source": "TechCrunch",       "category": "business", "trust": 80},
    {"url": "https://www.technologyreview.com/feed/","source": "MIT Tech Review", "category": "research", "trust": 90},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
                                                    "source": "The Verge",        "category": "product",  "trust": 78},

    # ポリシー・規制
    {"url": "https://artificialintelligenceact.eu/feed/",
                                                    "source": "EU AI Act",        "category": "policy",   "trust": 95},
    {"url": "https://www.nist.gov/artificial-intelligence/rss.xml",
                                                    "source": "NIST",             "category": "policy",   "trust": 97},
]

# スコアリング基準（Claude promptに渡す）
SCORING_CRITERIA = """
以下の基準で0-100のスコアをつけてください：
- 新規性: 既知の情報ではなく新しい発見・発表か (30点)
- 信頼性: 一次ソース・査読済み・公式発表か (25点)
- 重要性: AI分野全体に影響するか (25点)
- 実用性: 開発者・研究者・ビジネスパーソンに有益か (20点)

スコア基準:
90+ = CRITICAL: 即座に注目すべき重大ニュース
75-89 = HIGH: 重要なアップデート、広く共有する価値あり
60-74 = NORMAL: 参考になる情報
60未満 = SKIP: 掲載しない
"""

# ============================================================
# PIPELINE STEPS
# ============================================================

def fetch_feeds() -> list[dict]:
    """Step 1: RSSフィードから記事を収集"""
    articles = []
    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries[:5]:  # 各ソースから最大5件
                article_id = hashlib.md5(entry.get("link", "").encode()).hexdigest()
                articles.append({
                    "id": article_id,
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", entry.get("description", ""))[:2000],
                    "url": entry.get("link", ""),
                    "source": source["source"],
                    "source_trust": source["trust"],
                    "category": source["category"],
                    "published": entry.get("published", datetime.now(timezone.utc).isoformat()),
                })
            log.info(f"✓ {source['source']}: {len(feed.entries)} entries")
        except Exception as e:
            log.warning(f"✗ {source['source']}: {e}")
    log.info(f"Total fetched: {len(articles)} articles")
    return articles


def score_and_translate(articles: list[dict], client: anthropic.Anthropic) -> list[dict]:
    """Step 2: Claude APIでスコアリング + 日英記事生成"""
    results = []
    for article in articles:
        try:
            prompt = f"""
あなたはAI専門のジャーナリストです。以下の記事を評価・翻訳してください。

【タイトル】{article['title']}
【本文要約】{article['summary']}
【ソース】{article['source']} (信頼度スコア: {article['source_trust']}/100)
【カテゴリ】{article['category']}

{SCORING_CRITERIA}

以下のJSONフォーマットで回答してください（JSONのみ、マークダウン不要）:
{{
  "score": <0-100の整数>,
  "importance": <"critical"|"high"|"normal"|"skip">,
  "title_ja": <日本語タイトル（30字以内）>,
  "title_en": <英語タイトル（50 words以内）>,
  "summary_ja": <日本語要約（120字程度）>,
  "summary_en": <英語要約（100 words程度）>,
  "tags": <関連タグの配列 例: ["LLM", "OpenAI", "Benchmark"]>,
  "key_insight": <この記事の最重要ポイント1行（日本語）>
}}
"""
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()
            # JSONを安全にパース
            if "```" in raw:
                raw = raw.split("```")[1].replace("json", "").strip()
            scored = json.loads(raw)

            if scored.get("importance") == "skip" or scored.get("score", 0) < 60:
                log.info(f"SKIP (score={scored.get('score')}): {article['title'][:50]}")
                continue

            article.update(scored)
            article["processed_at"] = datetime.now(timezone.utc).isoformat()
            results.append(article)
            log.info(f"✓ score={scored['score']} [{scored['importance']}]: {scored['title_ja'][:40]}")

        except Exception as e:
            log.warning(f"Score error for {article['title'][:40]}: {e}")
    log.info(f"Passed scoring: {len(results)}/{len(articles)} articles")
    return results


def save_to_supabase(articles: list[dict], supabase_url: str, supabase_key: str) -> None:
    """Step 3: Supabaseに保存（重複スキップ）"""
    if not supabase_url or not supabase_key:
        log.warning("Supabase not configured, skipping DB save")
        return

    sb = create_client(supabase_url, supabase_key)
    for article in articles:
        try:
            sb.table("articles").upsert({
                "id": article["id"],
                "title_ja": article.get("title_ja", ""),
                "title_en": article.get("title_en", ""),
                "summary_ja": article.get("summary_ja", ""),
                "summary_en": article.get("summary_en", ""),
                "key_insight": article.get("key_insight", ""),
                "url": article["url"],
                "source": article["source"],
                "category": article["category"],
                "score": article.get("score", 0),
                "importance": article.get("importance", "normal"),
                "tags": article.get("tags", []),
                "processed_at": article.get("processed_at"),
            }, on_conflict="id").execute()
        except Exception as e:
            log.warning(f"DB error for {article.get('title_ja', '')}: {e}")
    log.info(f"Saved {len(articles)} articles to Supabase")


def post_to_twitter(articles: list[dict]) -> None:
    """Step 4: 重要記事のみXに投稿"""
    keys = [
        os.getenv("TWITTER_API_KEY"),
        os.getenv("TWITTER_API_SECRET"),
        os.getenv("TWITTER_ACCESS_TOKEN"),
        os.getenv("TWITTER_ACCESS_SECRET"),
    ]
    if not all(keys):
        log.warning("Twitter not configured, skipping posts")
        return

    client = tweepy.Client(
        consumer_key=keys[0],
        consumer_secret=keys[1],
        access_token=keys[2],
        access_token_secret=keys[3],
    )

    # CRITICALとHIGHのみ投稿（スパム防止）
    to_post = [a for a in articles if a.get("importance") in ("critical", "high")][:3]

    for article in to_post:
        score = article.get("score", 0)
        importance = article.get("importance", "").upper()
        key_insight = article.get("key_insight", "")
        title = article.get("title_ja", "")
        url = article.get("url", "")
        tags = " ".join([f"#{t.replace(' ','')}" for t in article.get("tags", [])[:3]])

        tweet = f"""🔔 [{importance}] {title}

{key_insight}

📊 Quality Score: {score}/100
🔗 {url}

{tags} #AINews #SIGNAL"""

        # 280文字制限チェック
        if len(tweet) > 280:
            tweet = tweet[:277] + "..."

        try:
            client.create_tweet(text=tweet)
            log.info(f"✓ Tweeted: {title[:40]}")
        except Exception as e:
            log.warning(f"Tweet error: {e}")


def generate_daily_digest(articles: list[dict], client: anthropic.Anthropic) -> str:
    """ボーナス: 1日1回のデイリーダイジェスト生成（ニュースレター用）"""
    top_articles = sorted(articles, key=lambda x: x.get("score", 0), reverse=True)[:5]
    summaries = "\n".join([f"- [{a['source']}] {a.get('title_ja', '')}: {a.get('key_insight', '')}"
                           for a in top_articles])

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": f"""以下の本日のAIニューストップ5から、ニュースレター用のデイリーダイジェストを作成してください。
読者は技術者からビジネスパーソンまで幅広く想定。簡潔かつ洞察に富んだ内容で。

{summaries}

フォーマット: 
- 件名（20字以内）
- 本文（300字程度、箇条書き可）
- 英語版も追記
"""
        }]
    )
    return response.content[0].text


# ============================================================
# MAIN
# ============================================================

def run_pipeline():
    log.info("=" * 50)
    log.info("SIGNAL Pipeline Started")
    log.info("=" * 50)

    # Init Claude
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required")
    claude = anthropic.Anthropic(api_key=api_key)

    # Step 1: Fetch
    articles = fetch_feeds()
    if not articles:
        log.error("No articles fetched")
        return

    # Step 2: Score & Translate
    scored = score_and_translate(articles, claude)
    if not scored:
        log.info("No articles passed scoring threshold")
        return

    # Step 3: Save to DB
    save_to_supabase(
        scored,
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_KEY", "")
    )

    # Step 4: Post to X
    post_to_twitter(scored)

    # Summary
    log.info("=" * 50)
    log.info(f"Pipeline complete: {len(scored)} articles published")
    for a in sorted(scored, key=lambda x: x.get("score", 0), reverse=True)[:5]:
        log.info(f"  [{a.get('score')}] {a.get('title_ja', '')[:50]}")
    log.info("=" * 50)


if __name__ == "__main__":
    run_pipeline()
