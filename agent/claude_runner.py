"""Claude CLI runner for generating suggested Jira comments."""

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

WORKDIR = Path(__file__).resolve().parent.parent / "workdir"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "suggest_comment.md"
CLAUDE_BIN = "/home/luke/.local/bin/claude"


def write_context_file(issue_key: str, detail: dict, comments: list[dict], *, days_in_status: int | None = None) -> Path:
    """Write ticket data and comments to a markdown file for Claude to read.

    Args:
        issue_key: Jira issue key.
        detail: Dict from get_issue_detail().
        comments: List of comment dicts from get_issue_comments().
        days_in_status: Days the ticket has been in its current status.

    Returns:
        Path to the written file.
    """
    WORKDIR.mkdir(exist_ok=True)
    path = WORKDIR / f"{issue_key}.md"

    sp = detail.get("story_points")
    sp_display = str(sp) if sp is not None else "NOT SET"

    # Expected duration: 1 SP = 1 day of work
    expected_days = int(sp) if sp is not None and sp > 0 else None

    assignee = detail.get("assignee")
    assignee_id = detail.get("assignee_account_id")
    if assignee and assignee_id:
        assignee_display = f"{assignee} | mention as: @[{assignee}]({assignee_id})"
    elif assignee:
        assignee_display = assignee
    else:
        assignee_display = "Unassigned"

    lines = [
        f"# {issue_key}: {detail.get('summary', '')}",
        "",
        f"**Status:** {detail.get('status', 'Unknown')}",
        f"**Assignee:** {assignee_display}",
        f"**Priority:** {detail.get('priority', 'Unknown')}",
        f"**Story Points:** {sp_display}",
        f"**Created:** {detail.get('created', 'Unknown')}",
        f"**Updated:** {detail.get('updated', 'Unknown')}",
    ]

    if days_in_status is not None:
        lines.append(f"**Days in current status:** {days_in_status}")
        if expected_days is not None:
            if days_in_status > expected_days:
                lines.append(f"**Schedule:** DELAYED (expected {expected_days}d based on SP, actual {days_in_status}d)")
            else:
                lines.append(f"**Schedule:** On track ({days_in_status}d of {expected_days}d expected)")

    if detail.get("due_date"):
        lines.append(f"**Due Date:** {detail['due_date']}")

    if detail.get("blockers"):
        blocker_strs = [f"{b['key']} ({b.get('status', '?')})" for b in detail["blockers"]]
        lines.append(f"**Blockers:** {', '.join(blocker_strs)}")

    if detail.get("description_snippet"):
        lines.extend(["", "## Description", "", detail["description_snippet"]])

    lines.extend(["", f"## Comments ({len(comments)})"])

    if not comments:
        lines.append("", )
        lines.append("No comments yet.")
    else:
        for c in comments:
            created = c.get("created", "")[:16].replace("T", " ")
            author = c.get("author", "Unknown")
            author_id = c.get("author_account_id", "")
            if author_id:
                author_display = f"{author} | mention as: @[{author}]({author_id})"
            else:
                author_display = author
            lines.extend([
                "",
                f"### {author_display} - {created}",
                "",
                c.get("body", ""),
            ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def run_claude_suggest(issue_key: str) -> dict:
    """Invoke Claude CLI to generate a suggested comment.

    Args:
        issue_key: Jira issue key (context file must already exist).

    Returns:
        Dict with 'summary' and 'suggested_comment' keys, or 'error'.
    """
    context_file = WORKDIR / f"{issue_key}.md"
    if not context_file.exists():
        return {"error": f"Context file not found for {issue_key}"}

    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")

    user_message = f"Read workdir/{issue_key}.md and follow your system prompt"

    # Clear env vars that prevent nested Claude Code sessions
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}

    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN,
        "--print",
        "-p", user_message,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--allowedTools", "Read",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        logger.error(f"Claude CLI failed for {issue_key}: {err_msg}")
        return {"error": f"Claude CLI error: {err_msg[:200]}"}

    raw = stdout.decode(errors="replace").strip()

    try:
        outer = json.loads(raw)
        # --output-format json wraps the response in {"result": "...", ...}
        text = outer.get("result", raw) if isinstance(outer, dict) else raw
    except json.JSONDecodeError:
        text = raw

    def _clean_dashes(d: dict) -> dict:
        """Replace em dashes with hyphens in text fields."""
        for key in ("summary", "suggested_comment"):
            if key in d and isinstance(d[key], str):
                d[key] = d[key].replace("\u2014", "-").replace("\u2013", "-")
        return d

    # Parse the inner JSON from Claude's response
    try:
        result = json.loads(text)
        if "summary" in result and "suggested_comment" in result:
            return _clean_dashes(result)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from text that might have surrounding prose
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            result = json.loads(text[start:end])
            if "summary" in result and "suggested_comment" in result:
                return _clean_dashes(result)
        except json.JSONDecodeError:
            pass

    return {"error": "Could not parse Claude response", "raw": text[:500]}
