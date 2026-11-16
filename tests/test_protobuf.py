import struct

import pytest

from episteme.anki.protobuf import Message, ProtobufError


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


class TestMessage:
    def test_integers(self):
        message = Message.parse(bytes([0x08, 150, 0x01, 0x10, 0]))  # field 1 = 150, field 2 = 0
        assert message.uint(1) == 150
        assert message.maybe_uint(2) == 0

    def test_a_missing_field_reads_as_its_default(self):
        message = Message.parse(b"")
        assert message.uint(1) == 0 and message.uint(1, 7) == 7
        assert message.boolean(1) is False
        assert message.float32(1) == 0.0 and message.float32(1, 2.5) == 2.5
        assert message.string(1) == ""
        assert message.float32s(1) == []
        assert message.message(1) is None

    def test_missing_differs_from_zero(self):
        message = Message.parse(bytes([0x08, 0]))
        assert message.maybe_uint(1) == 0
        assert message.maybe_uint(2) is None
        assert message.maybe_float32(1) is None  # a varint is not a float

    def test_the_last_value_of_a_repeated_field_wins(self):
        assert Message.parse(bytes([0x08, 1, 0x08, 2])).uint(1) == 2

    def test_booleans(self):
        assert Message.parse(bytes([0x08, 1])).boolean(1) is True

    def test_floats_are_the_number_that_was_written(self):
        message = Message.parse(varint(1 << 3 | 5) + struct.pack("<f", 0.9))
        assert message.float32(1) == 0.9

    def test_packed_floats(self):
        payload = struct.pack("<3f", 1.0, 10.0, 0.9)
        assert Message.parse(varint(1 << 3 | 2) + varint(len(payload)) + payload).float32s(1) == [1.0, 10.0, 0.9]

    def test_floats_written_one_by_one(self):
        data = (varint(1 << 3 | 5) + struct.pack("<f", 1.0)) * 2
        assert Message.parse(data).float32s(1) == [1.0, 1.0]

    def test_a_packed_list_that_ends_in_the_middle_of_a_float_is_an_error(self):
        with pytest.raises(ProtobufError, match="partial"):
            Message.parse(varint(1 << 3 | 2) + varint(3) + b"abc").float32s(1)

    def test_strings_are_utf8(self):
        data = "zażółć".encode()
        assert Message.parse(varint(1 << 3 | 2) + varint(len(data)) + data).string(1) == "zażółć"

    def test_nested_messages(self):
        inner = bytes([0x08, 7])
        outer = varint(2 << 3 | 2) + varint(len(inner)) + inner
        assert Message.parse(outer).message(2).uint(1) == 7

    def test_unknown_fields_are_skipped(self):
        data = bytes([0x08, 5]) + varint(99 << 3 | 0) + b"\x01" + varint(98 << 3 | 1) + b"12345678"
        assert Message.parse(data).uint(1) == 5

    @pytest.mark.parametrize("data", [
        bytes([0x08]),                      # a key with no value
        bytes([0x80]),                      # a varint that never ends
        bytes([0x0A, 5, 1, 2]),             # a length that runs past the end
        bytes([0x0D, 1, 2]),                # a 32-bit value cut short
        bytes([0x09, 1, 2, 3]),             # a 64-bit value cut short
        bytes([0x0B, 1]),                   # a start-group, which is not supported
    ])
    def test_broken_messages_are_errors(self, data: bytes):
        with pytest.raises(ProtobufError):
            Message.parse(data)

    def test_an_endless_number_is_an_error(self):
        with pytest.raises(ProtobufError, match="longer"):
            Message.parse(b"\xff" * 20 + b"\x01")
