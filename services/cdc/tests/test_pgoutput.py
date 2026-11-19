"""Decoder tests with hand-built pgoutput messages: no database needed."""

import struct

from cdc.pgoutput import Decoder, lsn_to_str, str_to_lsn


def text(s: str) -> bytes:
    return s.encode() + b"\0"


def tuple_data(*values: str | None) -> bytes:
    out = struct.pack(">h", len(values))
    for v in values:
        out += b"n" if v is None else b"t" + struct.pack(">i", len(v.encode())) + v.encode()
    return out


RELATION = (b"R" + struct.pack(">i", 16384) + text("public") + text("products") + b"d"
            + struct.pack(">h", 2)
            + b"\x01" + text("id") + struct.pack(">ii", 2950, -1)       # key column
            + b"\x00" + text("price_inr") + struct.pack(">ii", 23, -1))
BEGIN = b"B" + struct.pack(">qqi", 0x1000, 0, 742)
COMMIT = b"C" + b"\x00" + struct.pack(">qqq", 0x1000, 0x1028, 0)


def test_lsn_round_trip():
    assert lsn_to_str(0x194B6E8) == "0/194B6E8"
    assert str_to_lsn("1/0") == 1 << 32


def test_transaction_with_insert_update_delete():
    d = Decoder()
    for message in (RELATION, BEGIN,
                    b"I" + struct.pack(">i", 16384) + b"N" + tuple_data("a1", "12999"),
                    b"U" + struct.pack(">i", 16384) + b"N" + tuple_data("a1", "8999"),
                    b"D" + struct.pack(">i", 16384) + b"K" + tuple_data("a1", None)):
        assert d.feed(message) is None  # nothing is returned before the commit
    tx = d.feed(COMMIT)
    assert tx is not None and tx.xid == 742 and tx.end_lsn == "0/1028"
    assert [c.op for c in tx.changes] == ["insert", "update", "delete"]
    assert tx.changes[0].row == {"id": "a1", "price_inr": "12999"}
    assert tx.changes[1].key == {"id": "a1"} and tx.changes[1].row["price_inr"] == "8999"
    assert tx.changes[2].row is None and tx.changes[2].key == {"id": "a1"}