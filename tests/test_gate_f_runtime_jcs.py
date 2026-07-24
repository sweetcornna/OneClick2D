from __future__ import annotations

import unittest

from spikes.gate_f_runner.runtime import canonical_json_bytes


class RuntimeCanonicalJsonTests(unittest.TestCase):
    def test_rfc_8785_number_examples(self) -> None:
        self.assertEqual(
            b'[333333333.3333333,1e+30,4.5,0.002,1e-27,0.000001,1e-7,100000000000000000000,0]',
            canonical_json_bytes([333333333.33333329, 1e30, 4.5, 2e-3, 1e-27, 1e-6, 1e-7, 1e20, -0.0]),
        )

    def test_object_keys_use_utf16_order(self) -> None:
        value = {"": 1, "\U00010000": 2, "a": 3}
        self.assertEqual('{"a":3,"𐀀":2,"":1}'.encode("utf-8"), canonical_json_bytes(value))


if __name__ == "__main__":
    unittest.main()
