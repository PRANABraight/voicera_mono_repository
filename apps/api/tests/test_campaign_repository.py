"""Campaign repository unit tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pymongo import ReturnDocument

from app.services.campaign import campaign_repository as repo


@pytest.fixture(autouse=True)
def _clear_stores(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    campaigns: dict[str, dict[str, Any]] = {}
    queued: dict[str, dict[str, Any]] = {}

    class FakeCampaigns:
        def insert_one(self, doc: dict[str, Any]) -> None:
            campaigns[doc["campaign_id"]] = dict(doc)

        def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
            if "campaign_id" in query:
                doc = campaigns.get(query["campaign_id"])
                return dict(doc) if doc else None
            return None

        def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> MagicMock:
            doc = self.find_one(query)
            result = MagicMock()
            if not doc:
                result.matched_count = 0
                return result
            if "$set" in update:
                campaigns[doc["campaign_id"]].update(update["$set"])
            if "$push" in update:
                campaigns[doc["campaign_id"]].setdefault("logs", []).append(
                    update["$push"]["logs"]
                )
            result.matched_count = 1
            return result

        def find(self, query: dict[str, Any]):
            class Cursor(list):
                def sort(self, *_args, **_kwargs):
                    return self

            if "org_id" in query:
                matched = [dict(v) for v in campaigns.values() if v.get("org_id") == query.get("org_id")]
            elif "state" in query and "$in" in query["state"]:
                statuses = query["state"]["$in"]
                matched = [dict(v) for v in campaigns.values() if v.get("state") in statuses]
            else:
                matched = [dict(v) for v in campaigns.values()]
            return Cursor(matched)

        def delete_one(self, query: dict[str, Any]) -> MagicMock:
            result = MagicMock()
            cid = query.get("campaign_id")
            if cid in campaigns:
                del campaigns[cid]
                result.deleted_count = 1
            else:
                result.deleted_count = 0
            return result

    class FakeQueued:
        def __init__(self) -> None:
            self.docs: dict[str, dict[str, Any]] = queued

        def insert_many(self, docs: list[dict[str, Any]]) -> None:
            for doc in docs:
                self.docs[doc["queued_run_id"]] = dict(doc)

        def insert_one(self, doc: dict[str, Any]) -> None:
            self.docs[doc["queued_run_id"]] = dict(doc)

        def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
            qid = query.get("queued_run_id")
            doc = self.docs.get(qid)
            return dict(doc) if doc else None

        def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> MagicMock:
            qid = query.get("queued_run_id")
            result = MagicMock()
            doc = self.docs.get(qid)
            if not doc:
                result.matched_count = 0
                return result
            if "$set" in update:
                doc.update(update["$set"])
            result.matched_count = 1
            return result

        def find_one_and_update(self, query, update, sort=None, return_document=None):
            for qid, doc in list(self.docs.items()):
                if doc.get("campaign_id") != query.get("campaign_id"):
                    continue
                if doc.get("state") != query.get("state"):
                    continue
                scheduled = doc.get("scheduled_for")
                if scheduled is not None:
                    before = query["$or"][1]["scheduled_for"]["$lte"]
                    if scheduled > before:
                        continue
                updated = dict(doc)
                updated.update(update["$set"])
                self.docs[qid] = updated
                return updated
            return None

        def update_many(self, query, update):
            count = 0
            for qid, doc in self.docs.items():
                if doc.get("queued_run_id") in query["queued_run_id"]["$in"]:
                    doc.update(update["$set"])
                    count += 1
            result = MagicMock()
            result.modified_count = count
            return result

        def count_documents(self, query: dict[str, Any]) -> int:
            total = 0
            for doc in self.docs.values():
                if doc.get("campaign_id") != query.get("campaign_id"):
                    continue
                if "state" in query and doc.get("state") != query["state"]:
                    continue
                total += 1
            return total

        def delete_many(self, query: dict[str, Any]) -> MagicMock:
            cid = query.get("campaign_id")
            to_delete = [qid for qid, doc in self.docs.items() if doc.get("campaign_id") == cid]
            for qid in to_delete:
                del self.docs[qid]
            result = MagicMock()
            result.deleted_count = len(to_delete)
            return result

        def find(self, query: dict[str, Any]):
            cid = query.get("campaign_id")
            call_ids = set(query.get("call_id", {}).get("$in", []))
            return [
                dict(doc)
                for doc in self.docs.values()
                if doc.get("campaign_id") == cid and doc.get("call_id") in call_ids
            ]

    fake_org_db = {}

    class FakeOrganizations:
        def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
            return fake_org_db.get(query.get("org_id"))

    fake_db = {
        "Campaigns": FakeCampaigns(),
        "QueuedRuns": FakeQueued(),
        "Organizations": FakeOrganizations(),
    }

    def get_database():
        return fake_db

    monkeypatch.setattr(repo, "get_database", get_database)
    campaigns.clear()
    queued.clear()
    return fake_org_db


def test_create_and_get_campaign() -> None:
    doc = repo.create_campaign(
        {
            "org_id": "org-1",
            "name": "Test",
            "agent_id": "agent-1",
            "source_id": "campaigns/org-1/file.csv",
        }
    )
    fetched = repo.get_campaign_by_id(doc["campaign_id"])
    assert fetched is not None
    assert fetched["state"] == "created"


def test_bulk_create_and_claim_queued_runs() -> None:
    campaign = repo.create_campaign(
        {
            "org_id": "org-1",
            "name": "Dial",
            "agent_id": "agent-1",
            "source_id": "campaigns/org-1/file.csv",
        }
    )
    cid = campaign["campaign_id"]
    repo.bulk_create_queued_runs(
        [
            {
                "campaign_id": cid,
                "source_uuid": "row_1",
                "context_variables": {"phone_number": "+14155550001"},
            },
            {
                "campaign_id": cid,
                "source_uuid": "row_2",
                "context_variables": {"phone_number": "+14155550002"},
            },
        ]
    )
    claimed = repo.claim_queued_runs_for_processing(
        cid,
        scheduled_before=datetime.now(timezone.utc),
        limit=1,
    )
    assert len(claimed) == 1
    assert claimed[0]["state"] == "processing"


def test_get_org_concurrent_limit_uses_org_value(_clear_stores) -> None:
    _clear_stores["org-1"] = {"org_id": "org-1", "concurrent_call_limit": 7}
    assert repo.get_org_concurrent_limit("org-1") == 7


def test_get_org_concurrent_limit_falls_back_to_default(_clear_stores) -> None:
    from app.constants.campaign import DEFAULT_ORG_CONCURRENCY_LIMIT

    assert repo.get_org_concurrent_limit("org-missing") == DEFAULT_ORG_CONCURRENCY_LIMIT


def test_get_campaign_for_org_not_found_raises() -> None:
    with pytest.raises(repo.CampaignNotFoundError):
        repo.get_campaign_for_org("org-1", "missing")


def test_get_campaign_for_org_found() -> None:
    campaign = repo.create_campaign(
        {"org_id": "org-1", "name": "T", "agent_id": "a1", "source_id": "s1"}
    )
    fetched = repo.get_campaign_for_org("org-1", campaign["campaign_id"])
    assert fetched["campaign_id"] == campaign["campaign_id"]


def test_list_campaigns_filters_by_org() -> None:
    repo.create_campaign({"org_id": "org-1", "name": "A", "agent_id": "a1", "source_id": "s1"})
    repo.create_campaign({"org_id": "org-2", "name": "B", "agent_id": "a1", "source_id": "s1"})
    result = repo.list_campaigns("org-1")
    assert len(result) == 1
    assert result[0]["name"] == "A"


def test_update_campaign_not_found_returns_none() -> None:
    assert repo.update_campaign("missing") is None


def test_append_campaign_log_adds_entry() -> None:
    campaign = repo.create_campaign(
        {"org_id": "org-1", "name": "T", "agent_id": "a1", "source_id": "s1"}
    )
    repo.append_campaign_log(
        campaign["campaign_id"], level="info", event="test_event", message="hi"
    )
    fetched = repo.get_campaign_by_id(campaign["campaign_id"])
    assert len(fetched["logs"]) == 1
    assert fetched["logs"][0]["event"] == "test_event"


def test_increment_and_reset_campaign_metadata_counter() -> None:
    campaign = repo.create_campaign(
        {"org_id": "org-1", "name": "T", "agent_id": "a1", "source_id": "s1"}
    )
    cid = campaign["campaign_id"]
    assert repo.increment_campaign_metadata_counter(cid, "busy") == 1
    assert repo.increment_campaign_metadata_counter(cid, "busy") == 2
    repo.reset_campaign_metadata_counter(cid, "busy")
    fetched = repo.get_campaign_by_id(cid)
    assert "busy" not in fetched["orchestrator_metadata"]["counters"]


def test_increment_campaign_metadata_counter_missing_campaign_returns_zero() -> None:
    assert repo.increment_campaign_metadata_counter("missing", "busy") == 0


def test_return_processing_queued_runs_without_call() -> None:
    campaign = repo.create_campaign(
        {"org_id": "org-1", "name": "T", "agent_id": "a1", "source_id": "s1"}
    )
    cid = campaign["campaign_id"]
    repo.bulk_create_queued_runs(
        [{"campaign_id": cid, "source_uuid": "row_1", "context_variables": {"phone_number": "+1"}}]
    )
    claimed = repo.claim_queued_runs_for_processing(
        cid, scheduled_before=datetime.now(timezone.utc), limit=1
    )
    qid = claimed[0]["queued_run_id"]
    restored = repo.return_processing_queued_runs_without_call([qid])
    assert restored == 1
    assert repo.get_queued_run_by_id(qid)["state"] == "queued"


def test_return_processing_queued_runs_without_call_empty_list() -> None:
    assert repo.return_processing_queued_runs_without_call([]) == 0


def test_count_pending_and_processing_queued_runs() -> None:
    campaign = repo.create_campaign(
        {"org_id": "org-1", "name": "T", "agent_id": "a1", "source_id": "s1"}
    )
    cid = campaign["campaign_id"]
    repo.bulk_create_queued_runs(
        [{"campaign_id": cid, "source_uuid": "row_1", "context_variables": {"phone_number": "+1"}}]
    )
    before = datetime.now(timezone.utc).isoformat()
    assert repo.count_pending_queued_runs(cid, before) == 1
    assert repo.count_processing_queued_runs(cid) == 0
    repo.claim_queued_runs_for_processing(cid, scheduled_before=datetime.now(timezone.utc), limit=1)
    assert repo.count_processing_queued_runs(cid) == 1


def test_get_campaigns_by_status() -> None:
    c1 = repo.create_campaign({"org_id": "org-1", "name": "A", "agent_id": "a1", "source_id": "s1"})
    repo.update_campaign(c1["campaign_id"], state="running")
    repo.create_campaign({"org_id": "org-1", "name": "B", "agent_id": "a1", "source_id": "s1"})
    result = repo.get_campaigns_by_status(["running"])
    assert len(result) == 1
    assert result[0]["campaign_id"] == c1["campaign_id"]


def test_delete_campaign_and_queued_runs() -> None:
    campaign = repo.create_campaign(
        {"org_id": "org-1", "name": "T", "agent_id": "a1", "source_id": "s1"}
    )
    cid = campaign["campaign_id"]
    repo.bulk_create_queued_runs(
        [{"campaign_id": cid, "source_uuid": "row_1", "context_variables": {"phone_number": "+1"}}]
    )
    assert repo.delete_queued_runs_for_campaign(cid) == 1
    assert repo.delete_campaign(cid) is True
    assert repo.get_campaign_by_id(cid) is None


def test_get_redial_candidates_no_failed_calls_returns_empty() -> None:
    campaign = repo.create_campaign(
        {"org_id": "org-1", "name": "T", "agent_id": "a1", "source_id": "s1"}
    )
    with patch(
        "app.services.call_log_service.list_call_logs_by_campaign", return_value=[]
    ):
        result = repo.get_redial_candidates(campaign["campaign_id"])
    assert result == []


def test_get_redial_candidates_missing_campaign_returns_empty() -> None:
    assert repo.get_redial_candidates("missing") == []
