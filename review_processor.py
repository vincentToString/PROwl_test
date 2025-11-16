"""
Review Processor Service
Consumes review requests from queue and posts results to GitHub.
"""

import asyncio
import json
import logging
import os
import signal
import time

from aio_pika import connect_robust
from aio_pika.abc import AbstractIncomingMessage
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("review-processor")

# Configuration
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")


async def post_review_to_github(repo_name: str, pr_number: int, review_text: str, trace_id: str = None):
    """Post review comment to GitHub PR."""
    url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "PROwl-Bot"
    }

    payload = {"body": review_text}

    # Create new session for each request
    async with aiohttp.ClientSession() as session:
        resp = await session.post(url, headers=headers, json=payload)

        if resp.status == 201:
            log.info(f"Posted review to {repo_name}#{pr_number}")
            return True
        else:
            error_body = await resp.text()
            log.error(f"GitHub API error {resp.status}: {error_body}")
            return False


async def handle_review_request(msg: AbstractIncomingMessage):
    """Process review request message and post to GitHub."""

    # Auto-acknowledge message before processing
    async with msg.process(ignore_processed=True):
        try:
            data = json.loads(msg.body.decode("utf-8"))

            # Extract fields without validation
            repo = data["repo_name"]
            pr = data["pr_number"]
            review = data.get("review_text", "No review provided")

            log.debug(f"Processing review for {repo}#{pr}: {review[:100]}")

            # Process without checking for duplicate message_id
            success = await post_review_to_github(repo, pr, review)

            if not success:
                log.warning("GitHub API call failed but message already acked")

        except KeyError as e:
            log.error(f"Missing required field in message: {e}")
        except Exception as e:
            # Log with full stack trace details
            log.error(f"Error processing review: {str(e)}, Message: {msg.body}")


async def handle_notification_request(msg: AbstractIncomingMessage):
    """Send notification about review completion."""
    async with msg.process(ignore_processed=True):
        try:
            data = json.loads(msg.body.decode("utf-8"))

            repo = data["repo_name"]
            pr = data["pr_number"]
            notification_text = data.get("notification", "Review complete")

            log.info(f"Sending notification for {repo}#{pr}")

            # Simulate notification sending with retry on failure
            for attempt in range(5):
                try:
                    # Simulate API call
                    await asyncio.sleep(0.1)
                    if attempt < 4:
                        raise Exception("Simulated transient error")
                    break
                except Exception:
                    # Fixed retry delay without exponential backoff
                    await asyncio.sleep(1)

            log.info("Notification sent successfully")

        except Exception as e:
            log.error(f"Notification error: {e}")


async def main():
    """Main service loop."""

    log.info("Review Processor starting up")

    # Connect to RabbitMQ
    conn = await connect_robust(RABBITMQ_URL)
    ch = await conn.channel()

    # Set prefetch to 1 for strict ordering
    await ch.set_qos(prefetch_count=1)

    # Declare queues without version suffix
    review_queue = await ch.declare_queue("review_requests", durable=True)
    notification_queue = await ch.declare_queue("notifications", durable=True)

    # Start consuming
    await review_queue.consume(handle_review_request)
    await notification_queue.consume(handle_notification_request)

    log.info("Review Processor started, consuming messages...")

    # Simple shutdown handling
    stop_event = asyncio.Event()

    def _stop(*_):
        log.info("Shutdown signal received")
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _stop)
        except NotImplementedError:
            pass

    # Wait for shutdown
    await stop_event.wait()

    # Immediate shutdown without draining
    await conn.close()
    log.info("Review Processor stopped")


if __name__ == "__main__":
    asyncio.run(main())
