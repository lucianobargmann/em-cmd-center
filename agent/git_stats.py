"""Local git log stats collection.

Runs `git log --shortstat` on locally cloned repos to get lines committed
per author, replacing the slow Bitbucket diffstat API.
"""

import logging
import os
import re
import subprocess
from datetime import datetime

logger = logging.getLogger(__name__)


def fetch_all_remotes(repos_dir: str) -> None:
    """Run `git fetch --all --prune` in each repo under repos_dir."""
    if not repos_dir or not os.path.isdir(repos_dir):
        logger.warning(f"repos_dir not found: {repos_dir}")
        return

    for entry in os.listdir(repos_dir):
        repo_path = os.path.join(repos_dir, entry)
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            continue
        try:
            subprocess.run(
                ["git", "fetch", "--all", "--prune"],
                cwd=repo_path,
                capture_output=True,
                timeout=120,
            )
        except Exception as e:
            logger.warning(f"git fetch failed for {entry}: {e}")


def get_lines_by_author(
    repos_dir: str, since: datetime, until: datetime
) -> dict[str, int]:
    """Aggregate insertions + deletions per author email from local repos.

    Args:
        repos_dir: Path to directory containing cloned git repos.
        since: Start of date range (inclusive).
        until: End of date range (exclusive).

    Returns:
        Dict mapping author email (lowercase) to total lines (insertions + deletions).
        Also includes name-keyed entries as "name:<Author Name>" for fallback matching.
    """
    if not repos_dir or not os.path.isdir(repos_dir):
        logger.warning(f"repos_dir not found: {repos_dir}")
        return {}

    since_str = since.strftime("%Y-%m-%d")
    until_str = until.strftime("%Y-%m-%d")

    # email → total lines
    email_lines: dict[str, int] = {}
    # name → email (first seen mapping, for fallback)
    name_to_email: dict[str, str] = {}

    stat_re = re.compile(
        r"(\d+) insertions?\(\+\)|\d+ files? changed|(\d+) deletions?\(-\)"
    )

    for entry in os.listdir(repos_dir):
        repo_path = os.path.join(repos_dir, entry)
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            continue

        try:
            result = subprocess.run(
                [
                    "git", "log", "--all",
                    f"--since={since_str}",
                    f"--until={until_str}",
                    "--shortstat",
                    "--format=%aN <%aE>",
                ],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as e:
            logger.warning(f"git log failed for {entry}: {e}")
            continue

        current_author_email = None
        current_author_name = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue

            # Author line: "Name <email>"
            if "<" in line and ">" in line:
                email_match = re.search(r"<(.+?)>", line)
                if email_match:
                    current_author_email = email_match.group(1).lower()
                    current_author_name = line.split("<")[0].strip()
                    if current_author_name and current_author_email:
                        name_to_email[current_author_name.lower()] = current_author_email
                continue

            # Stat line: " 3 files changed, 120 insertions(+), 45 deletions(-)"
            if "changed" in line and current_author_email:
                insertions = 0
                deletions = 0
                ins_match = re.search(r"(\d+) insertions?\(\+\)", line)
                del_match = re.search(r"(\d+) deletions?\(-\)", line)
                if ins_match:
                    insertions = int(ins_match.group(1))
                if del_match:
                    deletions = int(del_match.group(1))
                email_lines[current_author_email] = (
                    email_lines.get(current_author_email, 0) + insertions + deletions
                )

    # Also return name-keyed entries for fallback matching
    result_dict = dict(email_lines)
    for name, email in name_to_email.items():
        if email in email_lines:
            result_dict[f"name:{name}"] = email_lines[email]

    return result_dict
