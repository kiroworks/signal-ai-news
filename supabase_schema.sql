-- ============================================================
-- SIGNAL AI News — Supabase Schema
-- Supabase SQL Editor で実行してください（何度実行しても安全）
-- ============================================================

-- articles テーブル（存在しない場合のみ作成）
CREATE TABLE IF NOT EXISTS articles (
  id           TEXT PRIMARY KEY,
  title_ja     TEXT NOT NULL,
  title_en     TEXT NOT NULL,
  summary_ja   TEXT,
  summary_en   TEXT,
  key_insight  TEXT,
  url          TEXT NOT NULL,
  source       TEXT NOT NULL,
  category     TEXT CHECK (category IN ('research', 'product', 'business', 'policy')),
  score        INTEGER CHECK (score >= 0 AND score <= 100),
  importance   TEXT CHECK (importance IN ('critical', 'high', 'normal')),
  tags         TEXT[],
  -- status: draft=レビュー待ち / published=公開中 / rejected=非掲載
  status       TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'rejected')),
  processed_at TIMESTAMPTZ DEFAULT NOW(),
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- 既存テーブルに status カラムを追加（既に存在する場合はスキップ）
ALTER TABLE articles ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft'
  CHECK (status IN ('draft', 'published', 'rejected'));

-- subscribers テーブル（存在しない場合のみ作成）
CREATE TABLE IF NOT EXISTS subscribers (
  id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  email      TEXT UNIQUE NOT NULL,
  language   TEXT DEFAULT 'ja',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  active     BOOLEAN DEFAULT true
);

-- ============================================================
-- RLS（Row Level Security）設定
-- ============================================================
ALTER TABLE articles    ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscribers ENABLE ROW LEVEL SECURITY;

-- 既存ポリシーを削除してから再作成（冪等性確保）
DROP POLICY IF EXISTS "Public read articles"  ON articles;
DROP POLICY IF EXISTS "Public insert subs"    ON subscribers;

-- articles: published のみ誰でも読める
CREATE POLICY "Public read articles"
  ON articles FOR SELECT
  USING (status = 'published');

-- subscribers: 誰でも登録できる
CREATE POLICY "Public insert subs"
  ON subscribers FOR INSERT
  WITH CHECK (true);

-- ============================================================
-- インデックス（パフォーマンス最適化）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_articles_status_score
  ON articles (status, score DESC);

CREATE INDEX IF NOT EXISTS idx_articles_category
  ON articles (category);

CREATE INDEX IF NOT EXISTS idx_articles_created_at
  ON articles (created_at DESC);

-- ============================================================
-- 確認クエリ（実行後にデータを確認する場合）
-- ============================================================
-- SELECT status, COUNT(*) FROM articles GROUP BY status;
-- SELECT * FROM articles ORDER BY created_at DESC LIMIT 10;
