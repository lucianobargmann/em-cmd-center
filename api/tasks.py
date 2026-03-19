"""Task CRUD REST API endpoints."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_db
from models import Task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    """Request body for creating a task."""

    title: str
    priority: str = "p2"
    category: str = "other"
    jira_key: str | None = None
    notes: str | None = None


class TaskUpdate(BaseModel):
    """Request body for updating a task (all fields optional)."""

    title: str | None = None
    priority: str | None = None
    category: str | None = None
    done: bool | None = None
    jira_key: str | None = None
    jira_url: str | None = None
    due_date: str | None = None
    notes: str | None = None


@router.get("")
def list_tasks(filter: str | None = None) -> list[dict]:
    """List all tasks with optional filtering.

    Args:
        filter: One of p1, p2, p3, open, done, auto.

    Returns:
        List of task dicts.
    """
    db = get_db()
    try:
        query = db.query(Task)

        if filter == "p1":
            query = query.filter(Task.priority == "p1")
        elif filter == "p2":
            query = query.filter(Task.priority == "p2")
        elif filter == "p3":
            query = query.filter(Task.priority == "p3")
        elif filter == "open":
            query = query.filter(Task.done == False)
        elif filter == "done":
            query = query.filter(Task.done == True)
        elif filter == "auto":
            query = query.filter(Task.auto == True)

        tasks = query.order_by(Task.done, Task.priority, Task.created_at).all()
        return [t.to_dict() for t in tasks]
    finally:
        db.close()


@router.post("")
def create_task(body: TaskCreate) -> dict:
    """Create a new user task.

    Args:
        body: Task creation data.

    Returns:
        Created task dict.
    """
    db = get_db()
    try:
        task = Task(
            title=body.title,
            priority=body.priority,
            category=body.category,
            jira_key=body.jira_key,
            notes=body.notes,
            source="user",
            auto=False,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task.to_dict()
    finally:
        db.close()


# NOTE: /done must be registered BEFORE /{task_id} to avoid route conflict
@router.delete("/done")
def clear_done_tasks() -> dict:
    """Delete all completed tasks.

    Returns:
        Dict with count of deleted tasks.
    """
    db = get_db()
    try:
        count = db.query(Task).filter(Task.done == True).delete()
        db.commit()
        return {"deleted": count}
    finally:
        db.close()


@router.patch("/{task_id}")
def update_task(task_id: str, body: TaskUpdate) -> dict:
    """Update a task's fields.

    Args:
        task_id: UUID of the task.
        body: Fields to update.

    Returns:
        Updated task dict.
    """
    db = get_db()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        update_data = body.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(task, key, value)
        task.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(task)
        return task.to_dict()
    finally:
        db.close()


@router.delete("/{task_id}")
def delete_task(task_id: str) -> dict:
    """Delete a single task.

    Args:
        task_id: UUID of the task.

    Returns:
        Confirmation dict.
    """
    db = get_db()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        db.delete(task)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/{task_id}/toggle")
def toggle_task(task_id: str) -> dict:
    """Toggle a task's done status.

    Args:
        task_id: UUID of the task.

    Returns:
        Updated task dict.
    """
    db = get_db()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        task.done = not task.done
        task.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        return task.to_dict()
    finally:
        db.close()
