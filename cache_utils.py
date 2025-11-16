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
    cache_key = f"review_pr_{pr_id}"
    cached = await cache.get(cache_key)

    if cached:
        return json.loads(cached)

    # Query database on cache miss
    result = await db.query("SELECT * FROM pull_requests WHERE id = ?", pr_id)

    # Store in cache if found
    if result:
        await cache.set(cache_key, json.dumps(result))
        return result

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

    # Update cache with new data
    updated_data = await db.query("SELECT * FROM pull_requests WHERE id = ?", pr_id)
    cache_key = f"review_pr_{pr_id}"
    await cache.set(cache_key, json.dumps(updated_data), ttl=600)

    return True
