# Reddit relationships crawler

这个脚本会抓取 `r/relationships` 的帖子评论树，并导出“直接回复数大于指定阈值”的评论线程。

默认规则：

- `source_post.number` 固定是 `1`
- 被选中的评论 `selected_comment.number` 固定是 `2`
- 被选中评论下面的直接回复按创建时间从早到晚排序，编号从 `3` 开始
- 默认只导出“直接回复数 > 2”的评论
- 同一个帖子下面如果有多条评论满足条件，会分别生成多条 `records`

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置 Reddit API

复制示例配置：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```env
REDDIT_CLIENT_ID=你的_client_id
REDDIT_CLIENT_SECRET=你的_client_secret
REDDIT_USER_AGENT=script:reddit_crawler:v1.0 (by u/你的reddit用户名)
```

## 运行

抓取 `r/relationships` 的热门帖子：

```bash
python reddit_crawler.py
```

抓取最新 100 个帖子，只导出直接回复数大于 2 的评论线程：

```bash
python reddit_crawler.py --listing new --limit 100 --min-replies 2
```

导出到指定文件：

```bash
python reddit_crawler.py --output data/relationships_comment_threads.json
```

## 常用参数

- `--subreddit relationships`：目标 subreddit，不要带 `r/`
- `--listing hot|new|top|rising|controversial`：抓取列表
- `--limit 25`：最多检查多少个帖子
- `--min-replies 2`：只导出直接回复数大于这个值的评论。默认 `2` 表示导出直接回复数 `> 2` 的评论
- `--top-time-filter week`：当 `--listing top` 或 `controversial` 时使用
- `--sleep 1`：每检查一个帖子后暂停 1 秒

## JSON 结构示例

```json
{
  "metadata": {
    "subreddit": "relationships",
    "listing": "hot",
    "limit": 25,
    "min_replies": 2,
    "reply_filter": "direct_reply_count > min_replies"
  },
  "records": [
    {
      "source_post": {
        "number": 1,
        "type": "post",
        "id": "abc123",
        "title": "Post title"
      },
      "selected_comment": {
        "number": 2,
        "type": "comment",
        "id": "def456",
        "direct_reply_count": 3,
        "body": "A comment with more than two direct replies"
      },
      "direct_replies": [
        {
          "number": 3,
          "type": "comment",
          "id": "ghi789",
          "parent_id": "t1_def456",
          "body": "First direct reply"
        }
      ]
    }
  ]
}
```
