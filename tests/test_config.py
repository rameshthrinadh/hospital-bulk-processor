import dataclasses

import pytest

from app.config import settings


def test_defaults_are_valid():
    assert settings.max_rows == 20
    assert settings.max_concurrency > 0


@pytest.mark.parametrize("field", ["max_concurrency", "max_attempts", "read_timeout", "max_rows"])
def test_non_positive_values_rejected(field):
    with pytest.raises(ValueError, match=field):
        dataclasses.replace(settings, **{field: 0})


def test_negative_backoff_rejected():
    with pytest.raises(ValueError, match="retry_backoff_seconds"):
        dataclasses.replace(settings, retry_backoff_seconds=-1)
