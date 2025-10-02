import asyncio
import json
import logging
import os
import signal

from aio_pika import connect_robust
from aio_pika.abc import AbstractIncomingMessage
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outbound-worker")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")      # needed for commenter
SLACK_TOKEN = os.getenv("SLACK_TOKEN")        # needed for notifier


# ----------------------
# Message Handlers
# ----------------------

async def handle_github(msg: AbstractIncomingMessage):
    """Handle PR review result and post to GitHub as a comment."""
    async with msg.process(ignore_processed=True):  # auto-ack on success
        try:
            data = json.loads(msg.body.decode("utf-8"))
            repo = data["repo_name"]
            pr = data["pr_number"]

            # Prefer review_text, fallback to summary
            review_body = data.get("review_text") or data.get("summary") or "[no review text]"

            log.info(f"Posting review to GitHub PR#{pr} in {repo}:\n{review_body[:200]}...")

            url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments"
            headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "PR-Owl-Bot"
            }

            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, headers=headers, json={"body": review_body})
                if resp.status != 201:
                    text = await resp.text()
                    log.error(f"GitHub API error {resp.status}: {text}")

        except Exception as e:
            log.error("Failed to handle GitHub message: %s", e, exc_info=True)
            await msg.nack(requeue=False)  # DLQ should capture


async def handle_slack(msg: AbstractIncomingMessage):
    """Handle PR review result and post to Slack channel."""
    async with msg.process(ignore_processed=True):
        try:
            data = json.loads(msg.body.decode("utf-8"))
            repo = data["repo_name"]
            pr = data["pr_number"]
            review_summary = data.get("summary", "[no summary]")

            log.info(f"Sending Slack notification for PR#{pr} in {repo}: {review_summary}")

            url = "https://slack.com/api/chat.postMessage"
            headers = {"Authorization": f"Bearer {SLACK_TOKEN}"}
            payload = {"channel": "#code-reviews", "text": review_summary}

            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, headers=headers, json=payload)
                if resp.status != 200:
                    text = await resp.text()
                    log.error(f"Slack API error {resp.status}: {text}")

        except Exception as e:
            log.error("Failed to handle Slack message: %s", e, exc_info=True)
            await msg.nack(requeue=False)


# ----------------------
# Worker Main Loop
# ----------------------

async def main():
    conn = await connect_robust(RABBITMQ_URL)
    ch = await conn.channel()
    await ch.set_qos(prefetch_count=5)

    github_q = await ch.declare_queue("github_comments", durable=True)
    slack_q = await ch.declare_queue("slack_msgs", durable=True)

    await github_q.consume(handle_github)
    await slack_q.consume(handle_slack)

    log.info("Outbound worker consuming from github_comments and slack_msgs queues...")

    stop_event = asyncio.Event()

    def _stop(*_):
        log.info("Shutdown signal received.")
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _stop)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
