"""Strict JSON profile and RFC 8785 canonicalization."""

from __future__ import annotations

import unittest

from oneclick2d.errors import StrictJsonError
from oneclick2d.strict_json import (
    canonical_bytes,
    canonical_sha256,
    is_sha256_hex,
    loads_strict,
    require_sha256_hex,
    sha256_hex,
)


class CanonicalizationTests(unittest.TestCase):
    def test_object_keys_sort_by_utf16_code_unit(self) -> None:
        self.assertEqual(canonical_bytes({"b": 1, "a": 2}), b'{"a":2,"b":1}')
        self.assertEqual(canonical_bytes({"ä": 1, "a": 2}), '{"a":2,"ä":1}'.encode())

    def test_numbers_use_the_shortest_interoperable_form(self) -> None:
        self.assertEqual(canonical_bytes([1, 1.5, -0.0, 1e21, 1e-7]), b"[1,1.5,0,1e+21,1e-7]")

    def test_control_characters_and_quotes_are_escaped(self) -> None:
        self.assertEqual(canonical_bytes({"s": 'a"b\\c\nd'}), b'{"s":"a\\"b\\\\c\\nd"}')

    def test_canonical_form_is_stable_through_a_parse_cycle(self) -> None:
        value = {"z": 1, "a": {"c": [True, False, None], "b": 2.5}}
        once = canonical_bytes(value)
        self.assertEqual(canonical_bytes(loads_strict(once)), once)

    def test_digest_is_taken_over_canonical_bytes(self) -> None:
        self.assertEqual(canonical_sha256({"a": 1, "b": 2}), canonical_sha256({"b": 2, "a": 1}))


class StrictProfileTests(unittest.TestCase):
    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaises(StrictJsonError):
            loads_strict(b'{"a":1,"a":2}')

    def test_non_finite_constants_are_rejected(self) -> None:
        for payload in (b'{"a":NaN}', b"[Infinity]", b"[-Infinity]"):
            with self.subTest(payload=payload), self.assertRaises(StrictJsonError):
                loads_strict(payload)

    def test_byte_order_mark_is_rejected(self) -> None:
        with self.assertRaises(StrictJsonError):
            loads_strict(b"\xef\xbb\xbf{}")

    def test_invalid_utf8_is_rejected(self) -> None:
        with self.assertRaises(StrictJsonError):
            loads_strict(b"\xff\xfe")

    def test_unsafe_integers_are_rejected(self) -> None:
        with self.assertRaises(StrictJsonError):
            loads_strict(b'{"a":9007199254740993}')

    def test_non_finite_float_cannot_be_serialized(self) -> None:
        with self.assertRaises(StrictJsonError):
            canonical_bytes({"a": float("inf")})

    def test_non_string_keys_are_rejected(self) -> None:
        with self.assertRaises(StrictJsonError):
            canonical_bytes({1: "a"})

    def test_unrepresentable_values_are_rejected(self) -> None:
        with self.assertRaises(StrictJsonError):
            canonical_bytes({"a": object()})


class DigestHelperTests(unittest.TestCase):
    def test_sha256_hex_is_lowercase_and_64_characters(self) -> None:
        digest = sha256_hex(b"payload")
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, digest.lower())

    def test_digest_recognition_rejects_uppercase_and_wrong_length(self) -> None:
        self.assertTrue(is_sha256_hex("a" * 64))
        self.assertFalse(is_sha256_hex("A" * 64))
        self.assertFalse(is_sha256_hex("a" * 63))
        self.assertFalse(is_sha256_hex(None))

    def test_require_digest_fails_closed(self) -> None:
        with self.assertRaises(StrictJsonError):
            require_sha256_hex("not-a-digest")


if __name__ == "__main__":
    unittest.main()
