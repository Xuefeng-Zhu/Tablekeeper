"""Domain-neutral, executable handoff lifecycle checks."""
from __future__ import annotations

from pathlib import Path

from .common import FactoryError

TRANSITIONS = {"PROPOSED": {"READY"}, "READY": {"IN_PROGRESS"}, "IN_PROGRESS": {"REVIEW"},
               "REVIEW": {"ACCEPTED", "REJECTED"}, "REJECTED": {"IN_PROGRESS"},
               "ACCEPTED": set(), "BLOCKED": set()}
REQUIRED = ("id", "owner", "dependencies", "state", "goal", "requirements", "starting_revision",
            "workspace_paths", "ownership_boundaries", "acceptance_conditions", "candidate_commit",
            "commands", "results", "evidence_paths", "limitations", "next_recipient")


def validate_item(item: dict, config: dict, transition: str | None = None) -> dict:
    if not isinstance(item, dict):
        raise FactoryError("Work item must be an object")
    missing = [key for key in REQUIRED if key not in item]
    if missing:
        raise FactoryError("Work item missing fields: " + ", ".join(missing))
    handles = {seat.get("handle", "").lstrip("@").lower() for seat in config["seats"] if seat.get("handle")}
    if not handles or not isinstance(item["owner"], str) or item["owner"].lstrip("@").lower() not in handles:
        raise FactoryError("Owner must be an actual configured BAND handle")
    if item["next_recipient"] and str(item["next_recipient"]).lstrip("@").lower() not in handles:
        raise FactoryError("Next recipient must be an actual configured BAND handle")
    for key in ("dependencies", "requirements", "workspace_paths", "acceptance_conditions", "commands", "results", "evidence_paths", "limitations"):
        if not isinstance(item[key], list):
            raise FactoryError(f"Work item {key} must be a list")
    for value in item["workspace_paths"] + item["evidence_paths"]:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise FactoryError("Workspaces and evidence paths must be absolute")
    state = item["state"]
    if state not in TRANSITIONS:
        raise FactoryError("Unknown work-item state")
    target = transition or state
    if transition and target != "BLOCKED" and target not in TRANSITIONS[state]:
        raise FactoryError(f"Invalid lifecycle transition {state} -> {target}")
    if target in ("READY", "IN_PROGRESS", "REVIEW", "ACCEPTED"):
        for key in ("goal", "requirements", "starting_revision", "workspace_paths", "ownership_boundaries", "acceptance_conditions"):
            if not item[key]:
                raise FactoryError(f"{target} requires {key}")
    if target in ("REVIEW", "ACCEPTED") and (not item["candidate_commit"] or not item["commands"] or not item["results"] or not item["evidence_paths"]):
        raise FactoryError(f"{target} requires an exact candidate, commands, results and evidence")
    if target in ("REJECTED", "BLOCKED") and (not item["evidence_paths"] or not item.get("bounded_next_action")):
        raise FactoryError(f"{target} requires evidence and a bounded_next_action")
    if target == "ACCEPTED" and (not item.get("reviewer")
            or str(item["reviewer"]).lstrip("@").lower() not in handles
            or str(item["reviewer"]).lstrip("@").lower() == item["owner"].lstrip("@").lower()
            or not item.get("room_event")):
        raise FactoryError("ACCEPTED requires an independent reviewer and room event reference")
    item = dict(item)
    item["state"] = target
    return item
