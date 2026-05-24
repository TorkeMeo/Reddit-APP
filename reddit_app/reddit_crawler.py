#!/usr/bin/env python3
"""把 Reddit 帖子和符合条件的评论线程导出为 JSON。

默认目标是 r/relationships。脚本会扫描每个帖子的评论树，找出“直接回复数
大于 --min-replies”的评论，然后把这些评论线程逐条导出。

每条导出的记录里：
- 源帖子编号固定为 1
- 被选中的评论编号固定为 2
- 这条评论下面的直接回复从 3 开始编号
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import praw
from dotenv import load_dotenv
from praw.models import MoreComments


# Reddit 列表类型。脚本会从这些列表里取帖子，例如 hot 表示热门，new 表示最新。
LISTINGS = ("hot", "new", "top", "rising", "controversial")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    例如：
    python reddit_crawler.py --listing new --limit 100 --min-replies 2

    上面这条命令表示：
    - 抓取 r/relationships 的最新帖子
    - 最多检查 100 个帖子
    - 只导出“直接回复数 > 2”的评论线程
    """
    parser = argparse.ArgumentParser(
        description="Crawl Reddit comment threads into numbered JSON."
    )
    parser.add_argument(
        "--subreddit",
        default="relationships",
        help="Subreddit name without r/ prefix. Default: relationships",
    )
    parser.add_argument(
        "--listing",
        default="hot",
        choices=LISTINGS,
        help="Listing to crawl. Default: hot",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum submissions to inspect from the listing. Default: 25",
    )
    parser.add_argument(
        "--min-replies",
        type=int,
        default=2,
        help=(
            "Only export comments with more than this many direct replies. "
            "Default: 2"
        ),
    )
    parser.add_argument(
        "--output",
        default="relationships_comment_threads.json",
        help="Output JSON path. Default: relationships_comment_threads.json",
    )
    parser.add_argument(
        "--top-time-filter",
        default="week",
        choices=("all", "day", "hour", "month", "week", "year"),
        help="Time filter used only when --listing top or controversial. Default: week",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep after each inspected submission. Default: 0",
    )
    return parser.parse_args()


def get_reddit_client() -> praw.Reddit:
    """创建 Reddit API 客户端。

    Reddit API 需要三个配置：
    - REDDIT_CLIENT_ID：应用的 client_id
    - REDDIT_CLIENT_SECRET：应用的 client_secret
    - REDDIT_USER_AGENT：应用标识，告诉 Reddit 这个脚本是谁、做什么

    这些值从 .env 文件或系统环境变量里读取，避免把密钥直接写进代码。
    """
    # load_dotenv 会自动读取当前目录下的 .env 文件。
    # 如果你已经在系统环境变量里设置了这些值，也可以不创建 .env。
    load_dotenv()

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")

    # 检查必须的配置是否缺失。缺任何一个都无法调用 Reddit API。
    missing = [
        name
        for name, value in (
            ("REDDIT_CLIENT_ID", client_id),
            ("REDDIT_CLIENT_SECRET", client_secret),
            ("REDDIT_USER_AGENT", user_agent),
        )
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    # 这里使用的是 PRAW 的只读模式：只传 client_id、client_secret、user_agent，
    # 不传 Reddit 用户名和密码，因此脚本只能读取公开数据，不会替你发帖或评论。
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


def get_submissions(
    subreddit: praw.models.Subreddit,
    listing: str,
    limit: int,
    time_filter: str,
) -> Any:
    """根据 listing 参数获取帖子列表。

    Reddit 的 top 和 controversial 需要 time_filter，例如 week、month、all。
    hot、new、rising 不需要 time_filter。
    """
    if listing in {"top", "controversial"}:
        return getattr(subreddit, listing)(limit=limit, time_filter=time_filter)
    return getattr(subreddit, listing)(limit=limit)


def normalize_text(text: str | None) -> str:
    """把 None 转成空字符串，避免 JSON 里出现不方便处理的 null 文本字段。"""
    return text or ""


def flatten_comments(submission: praw.models.Submission) -> list[praw.models.Comment]:
    """把一个帖子的嵌套评论树展开成普通列表。

    Reddit 的评论是树状结构：
    - 帖子下面有一级评论
    - 一级评论下面可能有回复
    - 回复下面还可以继续有回复

    PRAW 默认不会一次性加载全部评论，中间会有 MoreComments 占位符。
    replace_more(limit=None) 会尽量把这些占位符替换成真实评论。

    返回值是所有评论组成的列表，并按 created_utc 从早到晚排序。
    """
    # 设置为 old 可以让 Reddit API 尽量按旧到新返回评论。
    # 后面仍然会再 sort 一次，保证输出顺序稳定。
    submission.comment_sort = "old"

    # 把 "load more comments" 这样的占位符展开。
    # 注意：热门帖子评论很多时，这一步可能比较慢，也更容易触发 Reddit 速率限制。
    submission.comments.replace_more(limit=None)

    comments: list[praw.models.Comment] = []
    for comment in submission.comments.list():
        # 正常情况下 replace_more 后不应该剩很多 MoreComments，
        # 这里再判断一次，是为了让脚本更稳。
        if isinstance(comment, MoreComments):
            continue
        comments.append(comment)

    return sorted(comments, key=lambda comment: comment.created_utc)


def get_direct_replies(comment: praw.models.Comment) -> list[praw.models.Comment]:
    """获取某条评论下面的直接回复。

    这里的“直接回复”只统计当前评论下一层的回复，不统计更深层的孙回复。

    例如：
    A
    ├── B
    │   └── C
    └── D

    对 A 来说，直接回复是 B 和 D，数量是 2；C 不算 A 的直接回复。
    """
    replies = [
        reply for reply in comment.replies if not isinstance(reply, MoreComments)
    ]
    return sorted(replies, key=lambda reply: reply.created_utc)


def comment_to_json(comment: praw.models.Comment, number: int) -> dict[str, Any]:
    """把 PRAW 的 Comment 对象转换成普通 dict，方便写入 JSON。

    number 是我们自己定义的编号：
    - 被选中的评论固定传 2
    - 被选中评论下面的直接回复从 3 开始递增
    """
    return {
        "number": number,
        "type": "comment",
        "id": comment.id,
        "fullname": comment.fullname,
        "parent_id": comment.parent_id,
        "link_id": comment.link_id,
        "author": str(comment.author) if comment.author else None,
        "body": normalize_text(comment.body),
        "score": comment.score,
        "created_utc": comment.created_utc,
        "permalink": f"https://www.reddit.com{comment.permalink}",
    }


def submission_to_json(submission: praw.models.Submission) -> dict[str, Any]:
    """把 PRAW 的 Submission 帖子对象转换成普通 dict。

    在每条导出的 record 里，source_post 都是源帖子，并且 number 固定为 1。
    """
    return {
        "number": 1,
        "type": "post",
        "id": submission.id,
        "fullname": submission.fullname,
        "subreddit": str(submission.subreddit),
        "title": normalize_text(submission.title),
        "selftext": normalize_text(submission.selftext),
        "author": str(submission.author) if submission.author else None,
        "score": submission.score,
        "upvote_ratio": submission.upvote_ratio,
        "num_comments": submission.num_comments,
        "created_utc": submission.created_utc,
        "url": submission.url,
        "permalink": f"https://www.reddit.com{submission.permalink}",
    }


def selected_comment_to_json(
    comment: praw.models.Comment,
    direct_reply_count: int,
) -> dict[str, Any]:
    """把被筛选出来的评论转换成 JSON，并额外记录它的直接回复数量。"""
    data = comment_to_json(comment, number=2)
    data["direct_reply_count"] = direct_reply_count
    return data


def build_thread_records(
    submission: praw.models.Submission,
    min_replies: int,
) -> list[dict[str, Any]]:
    """从一个帖子里构造所有符合条件的评论线程记录。

    筛选条件是：
    len(某条评论的直接回复) > min_replies

    注意这里是“大于”，不是“大于等于”。
    默认 --min-replies 2 的意思是：只导出直接回复数为 3 条或更多的评论。

    如果同一个帖子里有 5 条评论都满足条件，就会返回 5 条 record。
    """
    source_post = submission_to_json(submission)
    records: list[dict[str, Any]] = []

    # flatten_comments 会拿到这个帖子下面所有层级的评论。
    # 所以不仅一级评论会被检查，二级、三级评论也会被检查。
    for comment in flatten_comments(submission):
        replies = get_direct_replies(comment)

        # 用户需求是“评论的评论数量大于 2”，所以这里使用 <= 跳过。
        # 如果 len(replies) 是 2，则不导出；如果是 3，则导出。
        if len(replies) <= min_replies:
            continue

        # 每个满足条件的评论单独存成一条记录：
        # - source_post：原始帖子，编号 1
        # - selected_comment：当前这条满足条件的评论，编号 2
        # - direct_replies：它下面的直接回复，编号 3、4、5...
        records.append(
            {
                "source_post": source_post,
                "selected_comment": selected_comment_to_json(
                    comment=comment,
                    direct_reply_count=len(replies),
                ),
                "direct_replies": [
                    comment_to_json(reply, number=index)
                    for index, reply in enumerate(replies, start=3)
                ],
            }
        )

    return records


def crawl(args: argparse.Namespace) -> dict[str, Any]:
    """主抓取流程。

    这个函数负责：
    1. 创建 Reddit 客户端
    2. 获取 subreddit 的帖子列表
    3. 逐个帖子扫描评论树
    4. 汇总所有满足条件的评论线程
    5. 组装最终 JSON 的 metadata 和 records
    """
    reddit = get_reddit_client()
    subreddit = reddit.subreddit(args.subreddit)
    submissions = get_submissions(
        subreddit=subreddit,
        listing=args.listing,
        limit=args.limit,
        time_filter=args.top_time_filter,
    )

    exported_records: list[dict[str, Any]] = []
    inspected_count = 0

    for submission in submissions:
        inspected_count += 1

        # 一个帖子可能导出 0 条、1 条或多条 record。
        # 例如帖子里有三条评论都拥有超过 2 条直接回复，就会追加三条记录。
        exported_records.extend(
            build_thread_records(
                submission=submission,
                min_replies=args.min_replies,
            )
        )
        if args.sleep > 0:
            time.sleep(args.sleep)

    # metadata 用来记录本次抓取参数和统计信息；
    # records 才是真正的数据内容。
    return {
        "metadata": {
            "subreddit": args.subreddit,
            "listing": args.listing,
            "limit": args.limit,
            "min_replies": args.min_replies,
            "reply_filter": "direct_reply_count > min_replies",
            "inspected_submissions": inspected_count,
            "exported_records": len(exported_records),
            "generated_utc": time.time(),
        },
        "records": exported_records,
    }


def main() -> int:
    """命令行入口函数。返回 0 表示成功，返回 1 表示失败。"""
    args = parse_args()

    try:
        data = crawl(args)
    except Exception as exc:
        # 把错误输出到 stderr，方便在终端或日志系统里区分正常输出和错误输出。
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output)

    # 如果用户传入 data/result.json，而 data 目录不存在，这里会自动创建。
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ensure_ascii=False 可以让中文正常写入 JSON，而不是变成 \u4e2d\u6587。
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Wrote {len(data['records'])} comment thread records to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
