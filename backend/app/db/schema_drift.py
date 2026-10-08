"""Compare live Postgres schemas with the model metadata (D12 drift check).

Alembic owns ``public``; ``tenancy.sync_firm_schema`` carries additive model
changes into each ``firm_<id>`` schema. Anything those two cannot express —
a NOT NULL column sync added as nullable, a drop or rename that skipped the
firm schemas, a hand-made hotfix — leaves a firm schema that no longer matches
the models. This module reports that difference per table:

* tables, columns and nullability (``public``: the control tables; each firm
  schema: the tenant tables);
* primary keys, unique keys (constraints and unique indexes alike), plain
  indexes and foreign keys, compared by their columns rather than by name —
  firm schemas legitimately use different index names (see
  ``tenancy._firm_metadata``).

Severity: anything the model expects but the database lacks, or a difference
that changes which writes succeed (nullability, an extra unique key or foreign
key, an extra NOT NULL column with no default), is an ERROR. Leftovers that
cannot reject a write (an extra table, nullable column or plain index) are
WARNINGS. Exclusion constraints, CHECK constraints, column types and server
defaults are out of scope.

Postgres only. Read-only: it never changes the database.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import Column, Index, Table, UniqueConstraint, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import Inspector

from app.db.base import Base
from app.db.tenancy import (
    CONTROL_TABLES,
    _firm_metadata,
    _register_models,
    is_postgres,
    tenant_tables,
)

Severity = Literal["error", "warning"]

# Bookkeeping tables that exist in the database but not on the models.
_UNMODELLED_PUBLIC = frozenset({"alembic_version"})


@dataclass(frozen=True)
class DriftIssue:
    schema: str
    table: str
    kind: str
    detail: str
    severity: Severity

    def line(self) -> str:
        where = f"{self.schema}.{self.table}"
        return f"  [{self.severity.upper()}] {where}: {self.kind} - {self.detail}"


@dataclass
class DriftReport:
    schemas: list[str] = field(default_factory=list)
    issues: list[DriftIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[DriftIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[DriftIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def failed(self, *, strict: bool = False) -> bool:
        return bool(self.errors or (strict and self.warnings))

    def render(self) -> str:
        head = (
            f"Schema drift check: {len(self.schemas)} schema(s), "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)."
        )
        if not self.issues:
            return head
        ordered = sorted(
            self.issues, key=lambda i: (i.severity != "error", i.schema, i.table, i.kind)
        )
        return "\n".join([head, *(i.line() for i in ordered)])


@dataclass(frozen=True)
class _Shape:
    """Comparable structure of one table, by column sets rather than names."""

    columns: dict[str, bool]  # name -> nullable
    defaulted: frozenset[str]
    primary_key: tuple[str, ...]
    unique: frozenset[tuple[tuple[str, ...], bool]]  # (columns, partial)
    indexes: frozenset[tuple[tuple[str, ...], bool]]
    foreign_keys: frozenset[tuple[tuple[str, ...], str, str, tuple[str, ...], str]]


def _index_columns(index: Index) -> tuple[str, ...]:
    names: list[str] = []
    for expr in index.expressions:
        name = getattr(expr, "name", None)
        plain = isinstance(expr, Column) and isinstance(name, str)
        names.append(name if plain and isinstance(name, str) else "<expr>")
    return tuple(names)


def _ondelete(value: str | None) -> str:
    return (value or "NO ACTION").upper()


def _model_shape(table: Table) -> _Shape:
    unique: set[tuple[tuple[str, ...], bool]] = set()
    indexes: set[tuple[tuple[str, ...], bool]] = set()
    for con in table.constraints:
        if isinstance(con, UniqueConstraint):
            unique.add((tuple(c.name for c in con.columns), False))
    for col in table.columns:
        if col.unique and not col.index:
            unique.add(((col.name,), False))
    for idx in table.indexes:
        partial = idx.dialect_options["postgresql"].get("where") is not None
        target = unique if idx.unique else indexes
        target.add((_index_columns(idx), partial))
    fks = {
        (
            tuple(c.name for c in fk.columns),
            fk.referred_table.schema or "public",
            fk.referred_table.name,
            tuple(e.column.name for e in fk.elements),
            _ondelete(fk.ondelete),
        )
        for fk in table.foreign_key_constraints
    }
    return _Shape(
        columns={c.name: bool(c.nullable) for c in table.columns},
        defaulted=frozenset(
            c.name for c in table.columns if c.server_default is not None or c.autoincrement is True
        ),
        primary_key=tuple(sorted(c.name for c in table.primary_key.columns)),
        unique=frozenset(unique),
        indexes=frozenset(indexes),
        foreign_keys=frozenset(fks),
    )


def _db_shape(insp: Inspector, schema: str, name: str) -> _Shape:
    columns = insp.get_columns(name, schema=schema)
    unique: set[tuple[tuple[str, ...], bool]] = {
        (tuple(c for c in u["column_names"] if c is not None), False)
        for u in insp.get_unique_constraints(name, schema=schema)
    }
    indexes: set[tuple[tuple[str, ...], bool]] = set()
    for idx in insp.get_indexes(name, schema=schema):
        if idx.get("duplicates_constraint"):
            continue
        cols = tuple(c if c is not None else "<expr>" for c in idx["column_names"])
        partial = bool((idx.get("dialect_options") or {}).get("postgresql_where"))
        (unique if idx.get("unique") else indexes).add((cols, partial))
    fks = {
        (
            tuple(fk["constrained_columns"]),
            fk.get("referred_schema") or "public",
            fk["referred_table"],
            tuple(fk["referred_columns"]),
            _ondelete((fk.get("options") or {}).get("ondelete")),
        )
        for fk in insp.get_foreign_keys(name, schema=schema)
    }
    pk = insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or []
    return _Shape(
        columns={c["name"]: bool(c["nullable"]) for c in columns},
        defaulted=frozenset(
            c["name"] for c in columns if c.get("default") is not None or c.get("identity")
        ),
        primary_key=tuple(sorted(pk)),
        unique=frozenset(unique),
        indexes=frozenset(indexes),
        foreign_keys=frozenset(fks),
    )


def _fmt_key(key: tuple[Any, ...]) -> str:
    cols, partial = key
    return f"({', '.join(cols)})" + (" WHERE ..." if partial else "")


def _fmt_fk(fk: tuple[Any, ...]) -> str:
    cols, ref_schema, ref_table, ref_cols, ondelete = fk
    return (
        f"({', '.join(cols)}) -> {ref_schema}.{ref_table}({', '.join(ref_cols)}) "
        f"ON DELETE {ondelete}"
    )


def _compare_columns(want: _Shape, have: _Shape, add: Any) -> None:
    for col, nullable in want.columns.items():
        if col not in have.columns:
            add("missing_column", col, "error")
        elif have.columns[col] != nullable:
            expected = "NULL" if nullable else "NOT NULL"
            actual = "NULL" if have.columns[col] else "NOT NULL"
            add("nullability", f"{col}: model {expected}, database {actual}", "error")
    for col, nullable in have.columns.items():
        if col in want.columns:
            continue
        blocking = not nullable and col not in have.defaulted
        add(
            "extra_column",
            col + (" (NOT NULL, no default: inserts fail)" if blocking else ""),
            "error" if blocking else "warning",
        )


def _compare_tables(schema: str, name: str, want: _Shape, have: _Shape) -> list[DriftIssue]:
    issues: list[DriftIssue] = []

    def add(kind: str, detail: str, severity: Severity) -> None:
        issues.append(DriftIssue(schema, name, kind, detail, severity))

    _compare_columns(want, have, add)
    if want.primary_key != have.primary_key:
        add(
            "primary_key",
            f"model ({', '.join(want.primary_key)}), database ({', '.join(have.primary_key)})",
            "error",
        )
    for key in sorted(want.unique - have.unique):
        add("missing_unique", _fmt_key(key), "error")
    for key in sorted(have.unique - want.unique):
        add("extra_unique", _fmt_key(key), "error")
    for key in sorted(want.indexes - have.indexes):
        add("missing_index", _fmt_key(key), "error")
    for key in sorted(have.indexes - want.indexes):
        add("extra_index", _fmt_key(key), "warning")
    for fk in sorted(want.foreign_keys - have.foreign_keys):
        add("missing_foreign_key", _fmt_fk(fk), "error")
    for fk in sorted(have.foreign_keys - want.foreign_keys):
        add("extra_foreign_key", _fmt_fk(fk), "error")
    return issues


def _check_schema(
    conn: Connection, schema: str, expected: Iterable[Table], *, ignore: frozenset[str]
) -> list[DriftIssue]:
    insp = sa_inspect(conn)
    present = set(insp.get_table_names(schema=schema))
    issues: list[DriftIssue] = []
    wanted = {t.name: t for t in expected}
    for name, table in sorted(wanted.items()):
        if name not in present:
            issues.append(DriftIssue(schema, name, "missing_table", "not in database", "error"))
            continue
        have = _db_shape(insp, schema, name)
        issues.extend(_compare_tables(schema, name, _model_shape(table), have))
    for name in sorted(present - set(wanted) - ignore):
        issues.append(DriftIssue(schema, name, "extra_table", "not on any model", "warning"))
    return issues


def firm_schemas(conn: Connection) -> list[str]:
    """Every ``firm_*`` schema present in the database."""
    rows = conn.execute(
        text(r"SELECT nspname FROM pg_namespace WHERE nspname LIKE 'firm\_%' ORDER BY nspname")
    )
    return [str(r[0]) for r in rows]


def check_public(conn: Connection) -> list[DriftIssue]:
    """The control tables in ``public``.

    ``public`` also keeps Alembic's copies of the tenant tables (the source of
    the global product catalogue each firm schema copies). No request routes
    to them, so only their presence matters here, not their shape.
    """
    _register_models()
    control = [t for t in Base.metadata.sorted_tables if t.name in CONTROL_TABLES]
    tenant_names = frozenset(t.name for t in tenant_tables())
    issues = _check_schema(
        conn, "public", control, ignore=_UNMODELLED_PUBLIC | tenant_names
    )
    present = set(sa_inspect(conn).get_table_names(schema="public"))
    issues.extend(
        DriftIssue("public", name, "missing_table", "not in database", "error")
        for name in sorted(tenant_names - present)
    )
    return issues


def check_firm_schema(conn: Connection, schema: str) -> list[DriftIssue]:
    staging = _firm_metadata(schema)
    expected = [t for t in staging.sorted_tables if t.schema == schema]
    return _check_schema(conn, schema, expected, ignore=frozenset())


def check_drift(conn: Connection, *, schemas: Iterable[str] | None = None) -> DriftReport:
    """Check ``public`` and every firm schema (or only ``schemas``)."""
    if not is_postgres(conn):
        raise RuntimeError("The schema drift check needs PostgreSQL.")
    tenant_tables()  # registers models before reading metadata
    report = DriftReport()
    targets = list(schemas) if schemas is not None else ["public", *firm_schemas(conn)]
    for schema in targets:
        report.schemas.append(schema)
        report.issues.extend(
            check_public(conn) if schema == "public" else check_firm_schema(conn, schema)
        )
    return report
