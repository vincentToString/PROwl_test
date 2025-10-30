import asyncio
from aio_pika.abc import AbstractIncomingMessage
import aio_pika
from aio_pika import Message, DeliveryMode
import os, json, argparse
from pathlib import Path
import orjson
import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field
import logging
import signal
from ai_service.models import PullRequestData, ReviewResult, Finding
from ai_service.config import Config
from ai_service.redis_client import RedisClient




logging.basicConfig(
      level=logging.INFO,
      format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'     
  )
logger = logging.getLogger(__name__)

redis_client = RedisClient(Config.REDIS_URL)

async def handle_message(message: AbstractIncomingMessage, channel):
    async with message.process(requeue=False): # manual ack
        event_dict = json.loads(message.body.decode("utf-8"))

        diff_id = event_dict.get("diff_id")

        if diff_id:
            logger.info(f"Retrieved diff from Redis with id{diff_id}")
            diff_content=await redis_client.get_diff(diff_id)

            if not diff_content:
                logger.error(f"Diff {diff_id} not found in Redis")
                return
            
            event_dict["pr_diff_content"] = diff_content
            logger.info(f"Retrieved diff from Redis, size: {len(diff_content)} bytes")
        else:
            logger.error(f"Diff id not presented, failed")
            return


        result = process_event(
            event_dict,
            prompt_path=Path(__file__).parent / "prompt.md",
            model=Config.MODEL,
            base_url=Config.OPENROUTER_BASE,
            api_key=Config.OPENROUTER_API_KEY,
            llm_timeout=Config.LLM_TIMEOUT,
            max_files=Config.MAX_FILES,
            max_lines=Config.MAX_LINES,
        )

        # if diff_id:
        #     await redis_client.delete_diff(diff_id)
        #     logger.info(f"Deleted diff {diff_id} from Redis")

        out_exchange = await channel.get_exchange("out_exchange")
        msg=Message(
            body=orjson.dumps(result.model_dump()),
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json", 
            headers={"repo": result.repo_name, "pr_number": result.pr_number},
        )
        await out_exchange.publish(msg, routing_key="")
        logger.info("Published review result for %s PR#%s", result.repo_name, result.pr_number)



            
def process_event(
    event_dict: dict,
    *,
    prompt_path: Path,
    model: str,
    base_url: str,
    api_key: str,
    llm_timeout: int,
    max_files: int,
    max_lines: int,
) -> ReviewResult:
    event = PullRequestData.model_validate(event_dict)
    if not event.pr_diff_content:
        logger.error(f"Receiving PR #{event.pr_number}has no diff content available")
        raise Exception(f"Invalid PR to review: #{event.pr_number}")
    files, snippets = parse_diff(
        event.pr_diff_content, max_files=max_files, max_lines_per_file=max_lines
    )

    prompt_template = load_prompt_template(prompt_path)
    prompt = render_prompt(prompt_template, event, files, snippets)
    llm_response = call_openrouter(
        prompt,
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_s=llm_timeout,
    )

    findings = [
        Finding.model_validate(finding)
        for finding in (llm_response.get("findings") or [])
    ]

    return ReviewResult(
        repo_name=event.repo_name,
        pr_number=event.pr_number,
        pr_url=event.pr_url,
        summary=(llm_response.get("summary") or "").strip(),
        findings=findings,
        guideline_references=[
            "Avoid secrets in code",
            "Add/adjust tests when behavior changes",
        ],
        llm_meta={"provider": "openrouter", "model": model},
    )

# Helper:
def parse_diff(diff_text: str, max_files: int, max_lines_per_file: int):
    """
    Parse unified diff into file metadata and code snippets.
    
    Args:
        diff_text: Raw unified diff from GitHub
        max_files: Maximum number of files to return
        max_lines_per_file: Maximum lines per file snippet
    
    Returns:
        (files, snippets): 
            - files: List of {filename, additions, deletions}
            - snippets: List of {filename, added_text, removed_text}
    """
    # Validation
    if not diff_text or not diff_text.strip():
        logger.warning("Empty diff provided")
        return [], []
    
    files = []
    snippets = []
    
    # Current file state
    current_file = None
    additions = 0
    deletions = 0
    added_lines = []
    removed_lines = []
    
    # Files to ignore (generated, minified, lock files)
    SKIP_PATTERNS = (
        "package-lock.json",
        "pnpm-lock.yaml", 
        "yarn.lock",
        "uv.lock",
        ".min.js",
        ".min.css",
        "dist/",
        "build/",
    )
    
    def should_skip_file(filename: str) -> bool:
        """Check if file should be excluded from review"""
        return any(pattern in filename for pattern in SKIP_PATTERNS)
    
    def save_file_data():
        """Save current file's data to results"""
        nonlocal current_file, additions, deletions, added_lines, removed_lines
        
        if current_file is None:
            return
        
        # Always save file metadata
        files.append({
            "filename": current_file,
            "additions": additions,
            "deletions": deletions,
        })
        
        # Only save snippets for non-noisy files with actual changes
        if not should_skip_file(current_file) and (added_lines or removed_lines):
            snippets.append({
                "filename": current_file,
                "added_text": "\n".join(added_lines[:max_lines_per_file]),
                "removed_text": "\n".join(removed_lines[:max_lines_per_file]),
            })
        
        # Reset state for next file
        current_file = None
        additions = 0
        deletions = 0
        added_lines = []
        removed_lines = []
    
    # Parse diff line by line
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # New file header - save previous file and reset
            save_file_data()
            current_file = None
        
        elif line.startswith("+++ b/"):
            # Extract filename (new version)
            current_file = line[6:].strip()  # Skip "+++ b/"
        
        elif line.startswith("--- a/"):
            # Old version filename - ignore
            pass
        
        elif line.startswith("@@"):
            # Hunk header - ignore
            pass
        
        else:
            # Only process if we have a current file
            if current_file is None:
                continue
            
            # Added line
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
                added_lines.append(line[1:])  # Remove '+' prefix
            
            # Deleted line
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
                removed_lines.append(line[1:])  # Remove '-' prefix
            
            # Context line (no prefix) - ignore for now
    
    # Don't forget the last file!
    save_file_data()
    
    # Sort by total impact (additions + deletions)
    files.sort(key=lambda f: f["additions"] + f["deletions"], reverse=True)
    
    # Select top N most-changed files
    top_files = files[:max_files]
    selected_filenames = {f["filename"] for f in top_files}
    
    # Filter snippets to only include top files
    top_snippets = [
        s for s in snippets 
        if s["filename"] in selected_filenames
    ][:max_files]
    
    logger.info(
        f"Parsed {len(files)} files, "
        f"selected {len(top_files)} top files, "
        f"{len(top_snippets)} snippets for review"
    )
    
    return top_files, top_snippets

def load_prompt_template(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"Prompt file not found: {path.resolve()}")

def render_prompt(
    prompt_template: str,
    event: PullRequestData,
    files: list[dict],
    snippets: list[dict],
) -> str:
    return (
        prompt_template.replace("{{repo_name}}", event.repo_name)
        .replace("{{pr_number}}", str(event.pr_number))
        .replace("{{pr_title}}", event.pr_title)
        .replace("{{pr_author}}", event.pr_author)
        .replace("{{pr_body}}", (event.pr_body or "")[:1000])
        .replace("{{files_table}}", build_files_table(files))
        .replace("{{snippets}}", build_snippets_block(snippets))
    )

def call_openrouter(
    prompt_text: str, model: str, base_url: str, api_key: str, timeout_s: int
) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://pr-demo.local",
        "X-Title": "AI PR Reviewer Demo",
    }

    body = {
        "model": model,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You are a precise code review assistant. Return ONLY JSON.",
            },
            {"role": "user", "content": prompt_text},
        ],
    }

    with httpx.Client(timeout=timeout_s, base_url=base_url) as client:
        response = client.post("/chat/completions", headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)

def build_files_table(files: list[dict]) -> str:
    return (
        "\n".join(
            f'{file_info["filename"]} +{file_info["additions"]}/-{file_info["deletions"]}'
            for file_info in files
        )
        or "(no files parsed)"
    )

def build_snippets_block(snippets: list[dict]) -> str:
    if not snippets:
        return "(no change snippets)"

    blocks = []
    for snippet in snippets:
        parts = [f"--- file: {snippet['filename']}"]

        added_text = snippet.get("added_text") or ""
        removed_text = snippet.get("removed_text") or ""

        if added_text:
            parts.append("\n".join("+" + line for line in added_text.splitlines()))
        if removed_text:
            parts.append("\n".join("-" + line for line in removed_text.splitlines()))

        blocks.append("\n".join(parts))

    return "\n".join(blocks)

async def main():
    conn = await aio_pika.connect_robust(Config.RABBITMQ_URL)
    channel = await conn.channel()

    await channel.set_qos(prefetch_count=1)

    pr_queue = await channel.declare_queue("pr_review", durable=True)

    stop_event = asyncio.Event()

    async def consumer(msg: AbstractIncomingMessage):
        await handle_message(msg, channel)

    await pr_queue.consume(consumer)
    logger.info("AI service consuming from pr_review queue")

    def _stop(*_):
        logger.info("Shut down")
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _stop)
        except NotImplementedError:
            pass

    await stop_event.wait()

    await redis_client.close()
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
