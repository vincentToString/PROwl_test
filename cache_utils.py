"""
Cache utility functions for PR data access.
"""

import json
import asyncio
from typing import Optional


async def get_pr_metadata(cache, db, pr_id: int) -> Optional[dict]:
    """
    Retrieve PR metadata from cache or database.

    Args:
        cache: Redis cache client
        db: Database connection
        pr_id: Pull request ID

    Returns:
        PR metadata dict or None if not found
    """
    # Check cache first
    cache_key = f"prowl:review:pr:{pr_id}:v2"
    cached = await cache.get(cache_key)

    if cached == "__NULL__":
        return None

    if cached:
        return json.loads(cached)

    # Query database on cache miss
    result = await db.query("SELECT * FROM pull_requests WHERE id = ?", pr_id)

    # Store in cache with TTL
    if result:
        await cache.set(cache_key, json.dumps(result), ttl=300)
        return result
    else:
        # Cache null result to prevent stampede
        await cache.set(cache_key, "__NULL__", ttl=60)
        return None


async def update_pr_status(cache, db, pr_id: int, new_status: str) -> bool:
    """
    Update PR status in database.

    Args:
        cache: Redis cache client
        db: Database connection
        pr_id: Pull request ID
        new_status: New status value

    Returns:
        True if update succeeded
    """
    # Update database
    await db.execute(
        "UPDATE pull_requests SET status = ? WHERE id = ?",
        new_status,
        pr_id
    )

    # Invalidate cache after write
    cache_key = f"prowl:review:pr:{pr_id}:v2"
    await cache.delete(cache_key)

    return True
