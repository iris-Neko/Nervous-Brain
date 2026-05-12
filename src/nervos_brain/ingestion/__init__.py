"""Ingestion layer — crawlers and data normalisation pipelines.

Current sources:
  discourse  — Nervos Talk (Discourse-based forum)
  github     — GitHub repository docs crawler
  github_code — GitHub repository source-code crawler
  jsonl      — generic local JSONL source
"""

from .base import RawDocument, SourceCrawler
from .discourse import DiscourseCrawler, DiscoursePost, TopicMeta
from .discourse_parallel import (
    ForumCrawlState,
    ForumCrawlStateStore,
    ParallelDiscourseIngestor,
    TopicCrawlResult,
    build_crawl_key,
)
from .github_docs import GitHubDocsCrawler, GitHubRepo
from .github_code import GitHubCodeCrawler
from .github_incremental import (
    GitHubIncrementalResult,
    GitHubIngestState,
    GitHubIngestStateStore,
    GitHubManifestWriter,
    GitHubRepoCrawler,
    JsonlExportCrawler as IncrementalJsonlExportCrawler,
    latest_commit_for_repo,
    reset_jsonl,
    resolve_repositories,
    run_incremental_github_ingest,
    targets_hash,
)
from .html_cleaner import html_to_text, make_summary
from .jsonl_crawler import JsonlCrawler
from .pipeline import IngestStats, IngestionPipeline

__all__ = [
    "RawDocument",
    "SourceCrawler",
    "DiscourseCrawler",
    "DiscoursePost",
    "TopicMeta",
    "ForumCrawlState",
    "ForumCrawlStateStore",
    "ParallelDiscourseIngestor",
    "TopicCrawlResult",
    "build_crawl_key",
    "GitHubDocsCrawler",
    "GitHubCodeCrawler",
    "GitHubRepo",
    "GitHubIncrementalResult",
    "GitHubIngestState",
    "GitHubIngestStateStore",
    "GitHubManifestWriter",
    "GitHubRepoCrawler",
    "IncrementalJsonlExportCrawler",
    "latest_commit_for_repo",
    "reset_jsonl",
    "resolve_repositories",
    "run_incremental_github_ingest",
    "targets_hash",
    "JsonlCrawler",
    "IngestStats",
    "IngestionPipeline",
    "html_to_text",
    "make_summary",
]
