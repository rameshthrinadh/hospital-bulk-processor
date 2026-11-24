import uuid

import httpx
import pytest

from app.csv_parser import HospitalRow
from app.directory_client import DirectoryClient, DirectoryError, ErrorKind
from tests.fake_directory import FakeDirectory

ROW = HospitalRow(1, "General Hospital", "1 Main St", "555-1234")


@pytest.fixture
def fake():
    return FakeDirectory()


@pytest.fixture
async def client(fake):
    async with fake.client() as http:
        yield DirectoryClient(http, max_attempts=3, retry_backoff_seconds=0)


@pytest.fixture
def batch_id():
    return str(uuid.uuid4())


def creates(fake):
    return fake.calls.count("POST /hospitals/")


# ---- create ---------------------------------------------------------------

async def test_create_sends_batch_id_and_returns_record(client, fake, batch_id):
    hospital = await client.create_hospital_safely(ROW, batch_id)
    assert hospital["name"] == "General Hospital"
    assert hospital["creation_batch_id"] == batch_id
    assert hospital["active"] is False
    assert creates(fake) == 1


async def test_validation_error_is_permanent_and_not_retried(client, fake, batch_id):
    fake.create_faults[ROW.name] = ["422"]
    with pytest.raises(DirectoryError) as exc_info:
        await client.create_hospital_safely(ROW, batch_id)
    assert exc_info.value.kind is ErrorKind.PERMANENT
    assert exc_info.value.status_code == 422
    assert creates(fake) == 1


async def test_pydantic_error_detail_is_readable(client, batch_id):
    with pytest.raises(DirectoryError) as exc_info:
        await client.create_hospital(HospitalRow(1, "", "x", None), batch_id)
    assert exc_info.value.message == "HTTP 422: name: String should have at least 1 character"


@pytest.mark.parametrize("fault", ["connect_error", "429"])
async def test_retryable_errors_are_retried(client, fake, batch_id, fault):
    fake.create_faults[ROW.name] = [fault]
    await client.create_hospital_safely(ROW, batch_id)
    assert creates(fake) == 2
    assert len(fake.batch(batch_id)) == 1


@pytest.mark.parametrize("fault", ["timeout", "500"])
async def test_ambiguous_error_where_nothing_landed_is_retried(client, fake, batch_id, fault):
    fake.create_faults[ROW.name] = [fault]
    await client.create_hospital_safely(ROW, batch_id)
    assert creates(fake) == 2
    assert len(fake.batch(batch_id)) == 1


async def test_lost_response_is_recovered_without_duplicate(client, fake, batch_id):
    # The create succeeds server-side but we time out reading the response.
    fake.create_faults[ROW.name] = ["lost_response"]
    hospital = await client.create_hospital_safely(ROW, batch_id)
    assert creates(fake) == 1  # no second POST
    assert len(fake.batch(batch_id)) == 1
    assert hospital["id"] == fake.batch(batch_id)[0]["id"]


async def test_recovery_only_adopts_an_exact_match(client, fake, batch_id):
    # Same name, different phone: a different hospital, not ours.
    await client.create_hospital(HospitalRow(2, ROW.name, ROW.address, "999"), batch_id)
    fake.create_faults[ROW.name] = ["timeout"]
    hospital = await client.create_hospital_safely(ROW, batch_id)
    assert hospital["phone"] == "555-1234"
    assert len(fake.batch(batch_id)) == 2


async def test_gives_up_after_max_attempts(client, fake, batch_id):
    fake.create_faults[ROW.name] = ["500", "500", "500"]
    with pytest.raises(DirectoryError) as exc_info:
        await client.create_hospital_safely(ROW, batch_id)
    assert exc_info.value.kind is ErrorKind.AMBIGUOUS
    assert creates(fake) == 3


async def test_retries_even_if_recovery_lookup_fails(client, fake, batch_id):
    fake.create_faults[ROW.name] = ["timeout"]
    fake.list_faults = ["500"]
    await client.create_hospital_safely(ROW, batch_id)
    assert len(fake.batch(batch_id)) == 1


async def test_read_timeout_is_classified_ambiguous(batch_id):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="http://x") as http:
        with pytest.raises(DirectoryError) as exc_info:
            await DirectoryClient(http).create_hospital(ROW, batch_id)
    assert exc_info.value.kind is ErrorKind.AMBIGUOUS


# ---- list -----------------------------------------------------------------

async def test_list_unknown_batch_is_empty_not_error(client, batch_id):
    assert await client.list_batch(batch_id) == []


# ---- activate -------------------------------------------------------------

async def make_batch(client, batch_id, n=2):
    for i in range(n):
        await client.create_hospital(HospitalRow(i + 1, f"H{i}", "St", None), batch_id)


async def test_activate_activates_all(client, fake, batch_id):
    await make_batch(client, batch_id)
    assert await client.activate_batch_safely(batch_id, expected_count=2) == 2
    assert all(h["active"] for h in fake.batch(batch_id))


async def test_activate_lost_response_is_success_not_error(client, fake, batch_id):
    # Activation went through but we never saw the 200. A retry would get
    # 400 "already active", which must not be reported as a failure.
    await make_batch(client, batch_id)
    fake.activate_faults = ["lost_response"]
    await client.activate_batch_safely(batch_id, expected_count=2)
    assert fake.calls.count(f"PATCH /hospitals/batch/{batch_id}/activate") == 1
    assert all(h["active"] for h in fake.batch(batch_id))


async def test_activate_retries_when_nothing_was_activated(client, fake, batch_id):
    await make_batch(client, batch_id)
    fake.activate_faults = ["timeout", "500"]
    await client.activate_batch_safely(batch_id, expected_count=2)
    assert fake.calls.count(f"PATCH /hospitals/batch/{batch_id}/activate") == 3
    assert all(h["active"] for h in fake.batch(batch_id))


async def test_activate_fails_after_max_attempts(client, fake, batch_id):
    await make_batch(client, batch_id)
    fake.activate_faults = ["500", "500", "500"]
    with pytest.raises(DirectoryError):
        await client.activate_batch_safely(batch_id, expected_count=2)
    assert not any(h["active"] for h in fake.batch(batch_id))


async def test_activate_count_mismatch_is_not_treated_as_success(client, fake, batch_id):
    # The batch is active but holds a hospital we didn't expect: report it.
    await make_batch(client, batch_id, n=3)
    fake.activate_faults = ["lost_response"]
    with pytest.raises(DirectoryError) as exc_info:
        await client.activate_batch_safely(batch_id, expected_count=2)
    assert "do not match" in exc_info.value.message


async def test_activate_empty_batch_is_permanent(client, fake, batch_id):
    with pytest.raises(DirectoryError) as exc_info:
        await client.activate_batch_safely(batch_id, expected_count=0)
    assert exc_info.value.status_code == 404
    assert fake.calls.count(f"PATCH /hospitals/batch/{batch_id}/activate") == 1


async def test_activate_200_without_count_is_verified_by_read_back(client, fake, batch_id):
    await make_batch(client, batch_id)
    fake.activate_faults = ["empty_200"]
    assert await client.activate_batch_safely(batch_id, expected_count=2) == 2
    assert f"GET /hospitals/batch/{batch_id}" in fake.calls


async def test_activate_lost_response_returns_read_back_count(client, fake, batch_id):
    await make_batch(client, batch_id, n=3)
    fake.activate_faults = ["lost_response"]
    assert await client.activate_batch_safely(batch_id, expected_count=3) == 3
