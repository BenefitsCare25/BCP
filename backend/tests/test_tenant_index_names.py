"""Regression for a real PostgreSQL identifier-truncation hash collision."""

from collections import Counter

import pytest
from sqlalchemy.dialects.postgresql import dialect

import app.models  # noqa: F401 -- register metadata
from app.db.base import Base
from app.db.tenancy import CONTROL_TABLES, _firm_metadata


@pytest.mark.parametrize("schema", [
    "firm_7169fecb6d034bd6b6644b374884c8ae",  # employee/policy-year indexes collided in CI
    "firm_11111111111111111111111111111111",
])
def test_firm_indexes_have_distinct_postgres_names_and_shared_policies(schema):
    metadata = _firm_metadata(schema)
    preparer = dialect().identifier_preparer
    names = Counter(
        (table.schema, preparer.format_index(index))
        for table in metadata.tables.values()
        for index in table.indexes
    )
    assert all(count == 1 for count in names.values())
    for table in metadata.tables.values():
        assert table.schema == (None if table.name in CONTROL_TABLES else schema)
    assert metadata.tables["ai_policy_versions"].schema is None
    # Copying must not modify the application's canonical metadata.
    assert all(table.schema is None for table in Base.metadata.tables.values())
