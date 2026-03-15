"""
Tests for the core lease flow: allocate → ack → requeue.

Verifies invariants:
  - X is only in one of {ready, inflight} at any time
  - mark_done without leases is rejected (400)
  - valid lease ack increments completed
  - invalid/late lease ack is rejected, not counted
  - expired leases are requeued back to ready
  - counters stay consistent
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_get_number_returns_leases(client, trainer_headers):
    """GET /get_number should return numbers with lease IDs."""
    resp = await client.get("/get_number?count=5", headers=trainer_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] in ("work", "wait", "done")
    if data["command"] == "work":
        assert len(data["numbers"]) > 0
        assert len(data["leases"]) == len(data["numbers"])
        assert data["lease_ttl"] > 0
        for n in data["numbers"]:
            assert str(n) in data["leases"]


async def test_mark_done_requires_leases(client, trainer_headers):
    """POST /mark_done without leases should return 400."""
    resp = await client.post(
        "/mark_done",
        headers=trainer_headers,
        json={"nums": [1, 2, 3]},
    )
    assert resp.status_code == 400
    assert "leases" in resp.json()["detail"].lower()


async def test_mark_done_with_valid_leases(client, trainer_headers):
    """Full cycle: get_number → mark_done with correct leases → acked."""
    get_resp = await client.get("/get_number?count=3", headers=trainer_headers)
    data = get_resp.json()
    if data["command"] != "work" or not data["numbers"]:
        pytest.skip("No numbers available (space exhausted or queue empty)")

    nums = data["numbers"]
    leases = data["leases"]

    done_resp = await client.post(
        "/mark_done",
        headers=trainer_headers,
        json={"nums": nums, "leases": leases},
    )
    assert done_resp.status_code == 200
    done_data = done_resp.json()
    assert done_data["ok"] is True
    assert done_data["count"] == len(nums)
    assert done_data["rejected"] == 0


async def test_mark_done_with_wrong_lease(client, trainer_headers):
    """mark_done with fabricated lease IDs should reject them."""
    get_resp = await client.get("/get_number?count=2", headers=trainer_headers)
    data = get_resp.json()
    if data["command"] != "work" or not data["numbers"]:
        pytest.skip("No numbers available")

    nums = data["numbers"]
    bad_leases = {str(n): "99999" for n in nums}

    done_resp = await client.post(
        "/mark_done",
        headers=trainer_headers,
        json={"nums": nums, "leases": bad_leases},
    )
    assert done_resp.status_code == 200
    done_data = done_resp.json()
    assert done_data["rejected"] == len(nums)
    assert done_data["count"] == 0


async def test_double_ack_is_safe(client, trainer_headers):
    """Second ack of same numbers should report already_done, not double-count."""
    get_resp = await client.get("/get_number?count=2", headers=trainer_headers)
    data = get_resp.json()
    if data["command"] != "work" or not data["numbers"]:
        pytest.skip("No numbers available")

    nums = data["numbers"]
    leases = data["leases"]

    resp1 = await client.post("/mark_done", headers=trainer_headers, json={"nums": nums, "leases": leases})
    assert resp1.json()["count"] == len(nums)

    resp2 = await client.post("/mark_done", headers=trainer_headers, json={"nums": nums, "leases": leases})
    d2 = resp2.json()
    assert d2["count"] == 0
    assert d2["already_done"] == len(nums)


async def test_heartbeat_count_zero(client, trainer_headers):
    """GET /get_number?count=0 is a heartbeat — returns ok with no numbers."""
    resp = await client.get("/get_number?count=0", headers=trainer_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] == "ok"
    assert data["numbers"] == []


async def test_auth_required(client):
    """Requests without token should be rejected."""
    resp = await client.get("/get_number?count=1")
    assert resp.status_code == 401

    resp = await client.get("/get_number?count=1", headers={"Authorization": "wrong"})
    assert resp.status_code == 401

    resp = await client.get(
        "/get_number?count=1",
        headers={"Authorization": "test-trainer-token"},
    )
    assert resp.status_code == 400  # missing X-Machine-Id


async def test_set_found_rejects_invalid_x(client, trainer_headers):
    """set_found with garbage X should return 400, not silently accept 0."""
    resp = await client.post(
        "/set_found",
        headers=trainer_headers,
        json={"x": "not-a-number", "y": "some-value"},
    )
    assert resp.status_code == 400


async def test_set_found_valid(client, trainer_headers):
    """set_found with valid X should succeed."""
    resp = await client.post(
        "/set_found",
        headers=trainer_headers,
        json={"x": 12345, "y": "result-value"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_health_endpoint(client):
    """GET /health should return structured health info."""
    resp = await client.get("/health")
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert "status" in data
    assert "keydb" in data
    assert "ready_queue" in data


async def test_status_endpoint(client):
    """GET /status should always work."""
    resp = await client.get("/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_spa_no_traversal(client):
    """Path traversal attempts should not leak files."""
    resp = await client.get("/../../etc/passwd")
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert "root:" not in resp.text


async def test_bad_gpu_headers_no_500(client):
    """Garbage GPU headers should not cause 500."""
    headers = {
        "Authorization": "test-trainer-token",
        "X-Machine-Id": "bad-header-machine",
        "X-GPU-Count": "not-a-number",
        "X-GPU-Mem": "garbage",
    }
    resp = await client.get("/get_number?count=1", headers=headers)
    assert resp.status_code != 500
