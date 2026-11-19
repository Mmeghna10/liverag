"""Decoder for pgoutput, the binary format of Postgres logical replication (protocol version 1).

Postgres sends one message per WAL event. The ones we use:
  R  Relation   describes a table (name and columns); sent before its first change
  B  Begin      a transaction starts
  I  Insert     a new row
  U  Update     a changed row (plus the old key if the key itself changed)
  D  Delete     a removed row (its key, or the whole old row with REPLICA IDENTITY FULL)
  T  Truncate   one or more tables emptied
  C  Commit     the transaction is complete; its end LSN is what we confirm later

Spec: https://www.postgresql.org/docs/16/protocol-logicalrep-message-formats.html
Values arrive as text (e.g. '12999', 't', '2026-11-19 14:00:59+00'); the consumer casts them.
"""

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Postgres timestamps count microseconds from 2000-01-01, not from 1970.
PG_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
UNCHANGED_TOAST = object()  # a large value that did not change and was not re-sent


def lsn_to_str(lsn: int) -> str:
    """64-bit LSN -> the usual '0/194B6E8' form."""
    return f"{lsn >> 32:X}/{lsn & 0xFFFFFFFF:X}"


def str_to_lsn(text: str) -> int:
    high, low = text.split("/")
    return (int(high, 16) << 32) | int(low, 16)


@dataclass
class Relation:
    oid: int
    schema: str
    name: str
    columns: list[str]
    key_columns: list[str]  # the replica identity (normally the primary key)


@dataclass
class Change:
    table: str               # "public.products"
    op: str                  # insert | update | delete | truncate
    key: dict[str, str | None]
    row: dict[str, str | None] | None  # new values (None for delete/truncate)


@dataclass
class Transaction:
    xid: int
    commit_lsn: str = ""
    end_lsn: str = ""
    commit_time: str = ""
    changes: list[Change] = field(default_factory=list)


class Reader:
    """Byte cursor over one message."""

    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def byte(self) -> str:
        self.pos += 1
        return chr(self.data[self.pos - 1])

    def int8(self) -> int:
        self.pos += 1
        return self.data[self.pos - 1]

    def int16(self) -> int:
        (value,) = struct.unpack_from(">h", self.data, self.pos)
        self.pos += 2
        return value

    def int32(self) -> int:
        (value,) = struct.unpack_from(">i", self.data, self.pos)
        self.pos += 4
        return value

    def int64(self) -> int:
        (value,) = struct.unpack_from(">q", self.data, self.pos)
        self.pos += 8
        return value

    def string(self) -> str:
        end = self.data.index(b"\0", self.pos)
        text = self.data[self.pos:end].decode()
        self.pos = end + 1
        return text

    def tuple(self, columns: list[str]) -> dict[str, object]:
        values: dict[str, object] = {}
        for name in columns[: self.int16()]:
            kind = self.byte()
            if kind == "n":
                values[name] = None
            elif kind == "u":
                values[name] = UNCHANGED_TOAST
            elif kind == "t":
                length = self.int32()
                values[name] = self.data[self.pos:self.pos + length].decode()
                self.pos += length
            else:
                raise ValueError(f"unknown tuple column kind {kind!r}")
        return values


class Decoder:
    """Turns the message stream into whole transactions. Feed it every message, in order."""

    def __init__(self) -> None:
        self.relations: dict[int, Relation] = {}
        self.current: Transaction | None = None

    def feed(self, payload: bytes) -> Transaction | None:
        """Decode one message; returns a Transaction when its Commit arrives, else None."""
        r = Reader(payload)
        kind = r.byte()

        if kind == "B":
            r.int64()  # final LSN of the transaction (repeated in Commit)
            r.int64()  # commit time (repeated in Commit)
            self.current = Transaction(xid=r.int32())
        elif kind == "C":
            r.int8()  # flags, unused
            tx, self.current = self.current, None
            assert tx is not None, "Commit without Begin"
            tx.commit_lsn = lsn_to_str(r.int64())
            tx.end_lsn = lsn_to_str(r.int64())
            tx.commit_time = (PG_EPOCH + timedelta(microseconds=r.int64())).isoformat()
            return tx
        elif kind == "R":
            oid = r.int32()
            schema, name = r.string() or "pg_catalog", r.string()
            r.int8()  # replica identity setting
            columns, keys = [], []
            for _ in range(r.int16()):
                is_key = r.int8() & 1
                column = r.string()
                r.int32()  # type OID
                r.int32()  # type modifier
                columns.append(column)
                if is_key:
                    keys.append(column)
            self.relations[oid] = Relation(oid, schema, name, columns, keys)
        elif kind in "IUD":
            self._row_change(kind, r)
        elif kind == "T":
            count = r.int32()
            r.int8()  # options (CASCADE / RESTART IDENTITY)
            for _ in range(count):
                rel = self.relations[r.int32()]
                self._add(Change(f"{rel.schema}.{rel.name}", "truncate", {}, None))
        # Other kinds (Origin, Type, logical Message) carry nothing we need.
        return None

    def _row_change(self, kind: str, r: Reader) -> None:
        rel = self.relations[r.int32()]
        table = f"{rel.schema}.{rel.name}"
        if kind == "I":
            r.byte()  # 'N' = new tuple follows
            row = r.tuple(rel.columns)
            self._add(Change(table, "insert", self._key(rel, row), self._clean(row)))
        elif kind == "U":
            old = None
            marker = r.byte()
            if marker in "KO":  # old key (K) or whole old row (O) was sent
                old = r.tuple(rel.columns)
                marker = r.byte()
            row = r.tuple(rel.columns)  # marker is 'N'
            key = self._key(rel, old) if old else self._key(rel, row)
            self._add(Change(table, "update", key, self._clean(row)))
        else:  # D
            r.byte()  # 'K' or 'O'
            old = r.tuple(rel.columns)
            self._add(Change(table, "delete", self._key(rel, old), None))

    def _add(self, change: Change) -> None:
        assert self.current is not None, "change outside a transaction"
        self.current.changes.append(change)

    @staticmethod
    def _key(rel: Relation, values: dict[str, object]) -> dict[str, str | None]:
        return {column: values.get(column) for column in rel.key_columns}  # type: ignore[misc]

    @staticmethod
    def _clean(values: dict[str, object]) -> dict[str, str | None]:
        """Drop unchanged TOAST columns: they were not sent, so we do not know their value here."""
        return {k: v for k, v in values.items() if v is not UNCHANGED_TOAST}  # type: ignore[misc]