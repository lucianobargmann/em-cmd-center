"""Claude CLI runner for generating suggested Jira comments."""

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

WORKDIR = Path(__file__).resolve().parent.parent / "workdir"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "suggest_comment.md"
CLAUDE_BIN = "/home/luke/.local/bin/claude"


def write_context_file(issue_key: str, detail: dict, comments: list[dict]) -> Path:
    """Write ticket data and comments to a markdown file for Claude to read.

    Args:
        issue_key: Jira issue key.
        detail: Dict from get_issue_detail().
        comments: List of comment dicts from get_issue_comments().

    Returns:
        Path to the written file.
    """
    WORKDIR.mkdir(exist_ok=True)
    path = WORKDIR / f"{issue_key}.md"

    lines = [
        f"# {issue_key}: {detail.get('summary', '')}",
        "",
        f"**Status:** {detail.get('status', 'Unknown')}",
        f"**Assignee:** {detail.get('assignee') or 'Unassigned'}",
        f"**Priority:** {detail.get('priority', 'Unknown')}",
        f"**Story Points:** {detail.get('story_points') or 'None'}",
        f"**Created:** {detail.get('created', 'Unknown')}",
        f"**Updated:** {detail.get('updated', 'Unknown')}",
    ]

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
            lines.extend([
                "",
                f"### {c.get('author', 'Unknown')} — {created}",
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

    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN,
        "--print",
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--allowedTools", "Read",
        user_message,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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

    # Parse the inner JSON from Claude's response
    try:
        result = json.loads(text)
        if "summary" in result and "suggested_comment" in result:
            return result
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from text that might have surrounding prose
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            result = json.loads(text[start:end])
            if "summary" in result and "suggested_comment" in result:
                return result
        except json.JSONDecodeError:
            pass

    return {"error": f"Could not parse Claude response", "raw": text[:500]}
