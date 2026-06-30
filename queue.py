from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ApplicationQueue:
    def __init__(self) -> None:
        self._next_id: int = 1
        self._applications: List[Dict[str, Any]] = []

    def add(self, group_id: Optional[str], applicant_qq: str) -> Optional[int]:
        for app in self._applications:
            if app["status"] != "pending":
                continue
            if app.get("group_id") == group_id and app["applicant_qq"] == applicant_qq:
                return None
        app_id = self._next_id
        self._next_id += 1
        self._applications.append({
            "id": app_id,
            "group_id": group_id,
            "applicant_qq": applicant_qq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        })
        return app_id

    def get_by_id(self, app_id: int) -> Optional[Dict[str, Any]]:
        for app in self._applications:
            if app["id"] == app_id:
                return app
        return None

    def get_pending(self) -> List[Dict[str, Any]]:
        return [a for a in self._applications if a["status"] == "pending"]

    def get_first_pending(self) -> Optional[Dict[str, Any]]:
        for app in self._applications:
            if app["status"] == "pending":
                return app
        return None

    def approve(self, app_id: int) -> bool:
        app = self.get_by_id(app_id)
        if app is None or app["status"] != "pending":
            return False
        app["status"] = "approved"
        return True

    def reject(self, app_id: int) -> bool:
        app = self.get_by_id(app_id)
        if app is None or app["status"] != "pending":
            return False
        app["status"] = "rejected"
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {"next_id": self._next_id, "applications": self._applications}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ApplicationQueue:
        q = cls()
        q._next_id = data.get("next_id", 1)
        q._applications = data.get("applications", [])
        return q
