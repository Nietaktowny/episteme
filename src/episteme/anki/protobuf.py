"""A minimal reader of the protobuf messages Anki stores in its collection.

Since schema 18 Anki keeps the options of a deck, and the kind of a deck, as protobuf blobs. Only
reading is needed, and only a handful of fields of a few messages, so this decodes the wire format
directly instead of depending on the ``protobuf`` package and Anki's generated classes. Fields that
are not asked for are skipped, which also makes it tolerant of fields newer Anki versions add.
"""

import struct
from typing import Self


class ProtobufError(ValueError):
    """The bytes are not a valid protobuf message."""


_VARINT, _FIXED64, _LENGTH_DELIMITED, _FIXED32 = 0, 1, 2, 5


def _varint(data: bytes, position: int) -> tuple[int, int]:
    """Read a varint at ``position``; return its value and the position after it."""
    value = shift = 0
    while True:
        if position >= len(data):
            raise ProtobufError("The message ends in the middle of a number.")
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7
        if shift > 70:
            raise ProtobufError("A number is longer than a protobuf allows.")


def _float(raw: bytes) -> float:
    """A 32-bit float as the shortest number that reads back as the same float (``0.9``, not ``0.8999999761581421``)."""
    return float(f"{struct.unpack('<f', raw)[0]:.7g}")


class Message:
    """A decoded protobuf message: its fields by number, read with the accessor that matches their type.

    A field that is missing reads as its default (``0``, ``False``, an empty list), as in proto3;
    :meth:`maybe_uint` and :meth:`maybe_float32` tell a missing field from a zero one.

    Examples:
        A message with ``new_per_day = 20`` (field 9, varint) and ``desired_retention = 0.9`` (field 37, float):

        >>> message = Message.parse(bytes([0x48, 20, 0xAD, 0x02]) + bytes.fromhex("6666663f"))
        >>> message.uint(9)
        20
        >>> message.maybe_uint(10) is None
        True
        >>> message.float32(37)
        0.9
    """

    def __init__(self, fields: dict[int, list[tuple[int, int | bytes]]]):
        self._fields = fields

    @classmethod
    def parse(cls, data: bytes) -> Self:
        """Decode a message.

        Raises:
            ProtobufError: If the bytes are not a well-formed message.
        """
        fields: dict[int, list[tuple[int, int | bytes]]] = {}
        position = 0
        while position < len(data):
            key, position = _varint(data, position)
            number, wire_type = key >> 3, key & 7
            if wire_type == _VARINT:
                value, position = _varint(data, position)
            elif wire_type == _LENGTH_DELIMITED:
                length, position = _varint(data, position)
                value, position = data[position:position + length], position + length
                if len(value) != length:
                    raise ProtobufError("The message ends in the middle of a field.")
            elif wire_type in (_FIXED32, _FIXED64):
                size = 4 if wire_type == _FIXED32 else 8
                value, position = data[position:position + size], position + size
                if len(value) != size:
                    raise ProtobufError("The message ends in the middle of a field.")
            else:
                raise ProtobufError(f"Unsupported protobuf wire type {wire_type} in field {number}.")
            fields.setdefault(number, []).append((wire_type, value))
        return cls(fields)

    def _last(self, number: int) -> tuple[int, int | bytes] | None:
        values = self._fields.get(number)
        return values[-1] if values else None

    def maybe_uint(self, number: int) -> int | None:
        """The integer (or enum, or bool) in a field, or ``None`` if the field is missing."""
        last = self._last(number)
        return last[1] if last is not None and last[0] == _VARINT else None

    def uint(self, number: int, default: int = 0) -> int:
        """The integer (or enum) in a field, ``default`` if the field is missing."""
        value = self.maybe_uint(number)
        return default if value is None else value

    def boolean(self, number: int) -> bool:
        """The bool in a field, ``False`` if the field is missing."""
        return bool(self.uint(number))

    def maybe_float32(self, number: int) -> float | None:
        """The 32-bit float in a field, or ``None`` if the field is missing."""
        last = self._last(number)
        return _float(last[1]) if last is not None and last[0] == _FIXED32 else None

    def float32(self, number: int, default: float = 0.0) -> float:
        """The 32-bit float in a field, ``default`` if the field is missing."""
        value = self.maybe_float32(number)
        return default if value is None else value

    def float32s(self, number: int) -> list[float]:
        """A repeated float field, whether it was written packed (the default) or one by one."""
        result: list[float] = []
        for wire_type, value in self._fields.get(number, []):
            if wire_type == _FIXED32:
                result.append(_float(value))
            elif wire_type == _LENGTH_DELIMITED:
                if len(value) % 4:
                    raise ProtobufError(f"A packed list of floats in field {number} has a partial item.")
                result.extend(_float(value[i:i + 4]) for i in range(0, len(value), 4))
        return result

    def string(self, number: int) -> str:
        """The text in a field, empty if the field is missing."""
        last = self._last(number)
        return last[1].decode("utf-8", errors="replace") if last is not None and last[0] == _LENGTH_DELIMITED else ""

    def message(self, number: int) -> Self | None:
        """The nested message in a field, or ``None`` if the field is missing."""
        last = self._last(number)
        return type(self).parse(last[1]) if last is not None and last[0] == _LENGTH_DELIMITED else None
