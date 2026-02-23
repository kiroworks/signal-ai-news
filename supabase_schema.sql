-- ============================================
-- SIGNAL DB Schema for Supabase
-- Supabase SQL Editorで実行してください
-- ============================================

-- 記事テーブル
CREATE TABLE IF NOT EXISTS articles (
  id           TEXT PRIMARY KEY,          -- MD5 hash of URL
  title_ja     TEXT NOT NULL,
  title_en     TEXT NOT NULL,
  summary_ja   TEXT,
  summary_en   TEXT,
  key_insight  TEXT,
  url          TEXT NOT NULL,
  source       TEXT NOT NULL,
  category     TEXT CHECK (category IN ('research','product','business','policy')),
  score        INTEGER CHECK (score >= 0 AND score <= 100),
  importance   TEXT CHECK (importance IN ('critical','high','normal')),
  tags         TEXT[],
  processed_at TIMESTAMPTZ DEFAULT NOW(),
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- パフォーマンス用インデックス
CREATE INDEX IF NOT EXISTS articles_score_idx      ON articles(score DESC);
CREATE INDEX IF NOT EXISTS articles_category_idx   ON articles(category);
CREATE INDEX IF NOT EXISTS articles_importance_idx ON articles(importance);
CREATE INDEX IF NOT EXISTS articles_created_idx    ON articles(created_at DESC);

-- ニュースレター購読者
CREATE TABLE IF NOT EXISTS subscribers (
  id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  email      TEXT UNIQUE NOT NULL,
  language   TEXT DEFAULT 'ja',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  active     BOOLEAN DEFAULT true
);

-- ページビュー（収益化のための分析用）
CREATE TABLE IF NOT EXISTS page_views (
  id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  article_id TEXT REFERENCES articles(id),
  viewed_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Row Level Security（公開読み取り可）
ALTER TABLE articles    ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscribers ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_views  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read articles"  ON articles    FOR SELECT USING (true);
CREATE POLICY "Public insert views"   ON page_views  FOR INSERT WITH CHECK (true);
CREATE POLICY "Public insert subs"    ON subscribers FOR INSERT WITH CHECK (true);

-- ============================================
-- 便利なVIEW（フロントエンドで使う）
-- ============================================

-- 今日の記事（スコア順）
CREATE OR REPLACE VIEW today_articles AS
SELECT * FROM articles
WHERE created_at >= NOW() - INTERVAL '24 hours'
ORDER BY score DESC;

-- カテゴリ別件数
CREATE OR REPLACE VIEW category_stats AS
SELECT category, COUNT(*) as count, AVG(score) as avg_score
FROM articles
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY category;
