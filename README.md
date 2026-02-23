# SIGNAL — セットアップガイド
## 「0円スタート → 収益化」完全ロードマップ

---

## 💰 コスト試算（月額）

| サービス       | 無料枠                      | 目安コスト        |
|--------------|---------------------------|-----------------|
| Vercel        | 無料（商用OK）               | **$0**          |
| Supabase      | 500MB / 2GB帯域 無料        | **$0**          |
| GitHub Actions| 2000分/月 無料              | **$0**          |
| Claude API    | 従量課金のみ                 | **$3〜15/月**   |
| X API (Basic) | 月1,500ツイート              | **$100/月** ※   |
| **合計**      |                             | **$3〜115/月**  |

※ X投稿は初期はスキップしてSNS手動運用でもOK。まずClaude APIだけ。

---

## 🚀 STEP 1: リポジトリ作成（5分）

```bash
# GitHubで新しいリポジトリ作成後
git clone https://github.com/あなたのID/signal-ai-news
cd signal-ai-news

# このプロジェクトのファイルをコピー
# index.html → ルートに配置
# scripts/ フォルダをそのまま配置
# .github/workflows/ フォルダをそのまま配置
```

---

## 🗄️ STEP 2: Supabase設定（10分）

1. https://supabase.com → 無料アカウント作成
2. 「New Project」作成
3. SQL Editor → `scripts/supabase_schema.sql` を貼り付けて実行
4. Settings → API → URLとanon keyをコピー

---

## ⚙️ STEP 3: GitHub Secrets設定（5分）

リポジトリ → Settings → Secrets and variables → Actions

```
ANTHROPIC_API_KEY   = sk-ant-...（https://console.anthropic.com）
SUPABASE_URL        = https://xxxx.supabase.co
SUPABASE_KEY        = eyJhbGc...（anon public key）
```

X投稿もする場合:
```
TWITTER_API_KEY         = ...
TWITTER_API_SECRET      = ...
TWITTER_ACCESS_TOKEN    = ...
TWITTER_ACCESS_SECRET   = ...
```

---

## 🌐 STEP 4: Vercelデプロイ（5分）

1. https://vercel.com → GitHubアカウントでログイン
2. 「New Project」→ GitHubリポジトリを選択
3. 「Deploy」ボタンを押すだけ
4. **カスタムドメイン**: signal-ai.jp などを取得して設定
   - Xdomain: .com/.jp ドメイン 年間数百円〜

---

## 📊 STEP 5: フロントエンドをSupabaseに接続

`index.html` の `newsData` 配列をAPIコールに置き換え:

```javascript
// index.htmlのfetchNews関数に追加
async function fetchNews(category = 'all') {
  const SUPABASE_URL = 'https://xxxx.supabase.co';
  const SUPABASE_KEY = 'eyJhbGc...';
  
  let url = `${SUPABASE_URL}/rest/v1/articles?order=score.desc&limit=20`;
  if (category !== 'all') url += `&category=eq.${category}`;
  
  const res = await fetch(url, {
    headers: {
      'apikey': SUPABASE_KEY,
      'Authorization': `Bearer ${SUPABASE_KEY}`
    }
  });
  return res.json();
}
```

---

## 💴 収益化ロードマップ

### Phase 1: 立ち上げ（0〜3ヶ月）
- ✅ サイト公開
- ✅ Google AdSense申請（審査あり、月1,000PVで$5〜20）
- ✅ X/SNSで宣伝（手動でも可）
- 目標: 月1,000 UU

### Phase 2: 成長（3〜6ヶ月）
- 📧 ニュースレター（Resend.com 無料枠3,000通/月）
- 🔗 アフィリエイト（AWS/Azure/Anthropic Partnerプログラム）
- 目標: 月10,000 UU、月1〜3万円

### Phase 3: マネタイズ本格化（6ヶ月〜）
- 💎 プレミアムプラン（月980円: 週次分析レポート、APIアクセス）
- 📢 スポンサーシップ（AIツール企業への直接営業）
- 🏢 法人プラン（社内情報共有ツールとして）
- 目標: 月5〜30万円

---

## 🔧 カスタマイズポイント

### ソースを追加したい場合
`scripts/news_pipeline.py` の `RSS_SOURCES` に追加:
```python
{"url": "追加したいRSSのURL",
 "source": "表示名",
 "category": "research|product|business|policy",
 "trust": 85},  # 信頼度0-100
```

### スコアリング基準を変えたい場合
`SCORING_CRITERIA` 変数を編集。
Claude APIへの指示なので日本語で自由に書けます。

### 更新頻度を変えたい場合
`.github/workflows/pipeline.yml` の `cron` を編集:
```yaml
- cron: '0 */2 * * *'  # 2時間おき
```

---

## 📈 SEO戦略

1. **静的生成**: 各記事にOGP/meta descriptionを自動生成
2. **サイトマップ**: `/sitemap.xml` をSupabaseクエリで動的生成
3. **構造化データ**: NewsArticle schema.orgを各記事に追加
4. **キーワード**: 「AIニュース 日本語」「最新AI情報」などでSEO

---

_SIGNAL — Built with Claude AI_
