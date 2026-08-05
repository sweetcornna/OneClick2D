"""Standard-library JSON Schema draft 2020-12 validator (bounded subset)."""

from __future__ import annotations

import unittest
from pathlib import Path

from oneclick2d.errors import ContractError
from oneclick2d.jsonschema import SchemaError, Validator, load_validator

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"


class RepositorySchemaTests(unittest.TestCase):
    def test_every_repository_schema_compiles(self) -> None:
        """Compilation rejects unimplemented keywords, so this proves the
        validator actually enforces every constraint the repo's schemas declare
        rather than silently ignoring some of them.
        """
        paths = sorted(SCHEMA_ROOT.rglob("*.json"))
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(schema=str(path.relative_to(SCHEMA_ROOT))):
                load_validator(path)

    def test_cir_schema_rejects_an_empty_document(self) -> None:
        validator = load_validator(SCHEMA_ROOT / "cir/v0.2/project.schema.json")
        self.assertTrue(validator.validate({}))


class KeywordTests(unittest.TestCase):
    def test_object_keywords(self) -> None:
        validator = Validator(
            {
                "type": "object",
                "properties": {"a": {"type": "integer"}},
                "required": ["a"],
                "additionalProperties": False,
            }
        )
        self.assertEqual(validator.validate({"a": 1}), [])
        self.assertTrue(validator.validate({}))
        self.assertTrue(validator.validate({"a": 1, "b": 2}))
        self.assertTrue(validator.validate({"a": "x"}))

    def test_integer_accepts_integral_floats_but_not_booleans(self) -> None:
        validator = Validator({"type": "integer"})
        self.assertEqual(validator.validate(1.0), [])
        self.assertTrue(validator.validate(1.5))
        self.assertTrue(validator.validate(True))

    def test_number_rejects_booleans(self) -> None:
        self.assertTrue(Validator({"type": "number"}).validate(True))

    def test_numeric_bounds(self) -> None:
        validator = Validator({"type": "number", "minimum": 0, "exclusiveMaximum": 1})
        self.assertEqual(validator.validate(0), [])
        self.assertTrue(validator.validate(1))
        self.assertTrue(validator.validate(-0.1))

    def test_multiple_of(self) -> None:
        validator = Validator({"multipleOf": 0.5})
        self.assertEqual(validator.validate(1.5), [])
        self.assertTrue(validator.validate(1.3))

    def test_array_keywords(self) -> None:
        validator = Validator(
            {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}
        )
        self.assertEqual(validator.validate(["a", "b"]), [])
        self.assertTrue(validator.validate([]))
        self.assertTrue(validator.validate(["a", "a"]))

    def test_prefix_items_then_items(self) -> None:
        validator = Validator(
            {"type": "array", "prefixItems": [{"type": "integer"}], "items": {"type": "string"}}
        )
        self.assertEqual(validator.validate([1, "a"]), [])
        self.assertTrue(validator.validate(["a", "a"]))
        self.assertTrue(validator.validate([1, 2]))

    def test_const_uses_json_equality(self) -> None:
        self.assertEqual(Validator({"const": 1}).validate(1.0), [])
        self.assertTrue(Validator({"const": 1}).validate(True))
        self.assertTrue(Validator({"const": True}).validate(1))

    def test_enum(self) -> None:
        validator = Validator({"enum": ["a", "b"]})
        self.assertEqual(validator.validate("a"), [])
        self.assertTrue(validator.validate("c"))

    def test_local_ref_resolution(self) -> None:
        validator = Validator(
            {"$defs": {"n": {"type": "integer"}}, "properties": {"x": {"$ref": "#/$defs/n"}}}
        )
        self.assertEqual(validator.validate({"x": 3}), [])
        self.assertTrue(validator.validate({"x": "s"}))

    def test_unresolvable_ref_fails_closed(self) -> None:
        validator = Validator({"properties": {"x": {"$ref": "#/$defs/missing"}}})
        with self.assertRaises(SchemaError):
            validator.validate({"x": 1})

    def test_one_of_requires_exactly_one_match(self) -> None:
        validator = Validator({"oneOf": [{"type": "integer"}, {"type": "string"}]})
        self.assertEqual(validator.validate(1), [])
        self.assertTrue(validator.validate(None))
        overlapping = Validator({"oneOf": [{"type": "number"}, {"type": "integer"}]})
        self.assertTrue(overlapping.validate(1))

    def test_all_of_and_any_of(self) -> None:
        self.assertEqual(Validator({"allOf": [{"type": "integer"}, {"minimum": 0}]}).validate(1), [])
        self.assertTrue(Validator({"allOf": [{"type": "integer"}, {"minimum": 0}]}).validate(-1))
        self.assertEqual(Validator({"anyOf": [{"type": "integer"}, {"type": "string"}]}).validate("s"), [])
        self.assertTrue(Validator({"anyOf": [{"type": "integer"}]}).validate("s"))

    def test_not(self) -> None:
        validator = Validator({"not": {"type": "string"}})
        self.assertEqual(validator.validate(1), [])
        self.assertTrue(validator.validate("s"))

    def test_if_then_else(self) -> None:
        validator = Validator(
            {
                "if": {"properties": {"k": {"const": "a"}}, "required": ["k"]},
                "then": {"required": ["extra"]},
            }
        )
        self.assertTrue(validator.validate({"k": "a"}))
        self.assertEqual(validator.validate({"k": "a", "extra": 1}), [])
        self.assertEqual(validator.validate({"k": "b"}), [])

    def test_string_keywords(self) -> None:
        validator = Validator({"type": "string", "pattern": "^[a-z]+$", "minLength": 2})
        self.assertEqual(validator.validate("abc"), [])
        self.assertTrue(validator.validate("A1"))
        self.assertTrue(validator.validate("a"))

    def test_pattern_properties_gate_additional_properties(self) -> None:
        validator = Validator(
            {
                "type": "object",
                "patternProperties": {"^x-": {"type": "integer"}},
                "additionalProperties": False,
            }
        )
        self.assertEqual(validator.validate({"x-a": 1}), [])
        self.assertTrue(validator.validate({"x-a": "s"}))
        self.assertTrue(validator.validate({"y": 1}))

    def test_contains(self) -> None:
        validator = Validator({"type": "array", "contains": {"const": 7}})
        self.assertEqual(validator.validate([1, 7]), [])
        self.assertTrue(validator.validate([1, 2]))

    def test_property_count_bounds(self) -> None:
        validator = Validator({"type": "object", "minProperties": 1, "maxProperties": 2})
        self.assertEqual(validator.validate({"a": 1}), [])
        self.assertTrue(validator.validate({}))
        self.assertTrue(validator.validate({"a": 1, "b": 2, "c": 3}))


class FailClosedTests(unittest.TestCase):
    def test_unimplemented_keywords_are_rejected_at_compile_time(self) -> None:
        """An ignored keyword would turn a real constraint into a rubber stamp,
        so the validator refuses to compile a schema it cannot fully enforce.
        """
        for keyword in (
            "dependentSchemas",
            "dependentRequired",
            "unevaluatedProperties",
            "unevaluatedItems",
            "$dynamicRef",
            "propertyDependencies",
        ):
            with self.subTest(keyword=keyword), self.assertRaises(SchemaError):
                Validator({keyword: {}})

    def test_unsupported_type_name_is_rejected(self) -> None:
        with self.assertRaises(SchemaError):
            Validator({"type": "tuple"}).validate([])

    def test_non_object_schema_is_rejected(self) -> None:
        with self.assertRaises(SchemaError):
            Validator([])  # type: ignore[arg-type]

    def test_check_raises_contract_error(self) -> None:
        with self.assertRaises(ContractError):
            Validator({"type": "integer"}).check("x", label="document")

    def test_false_schema_permits_nothing(self) -> None:
        self.assertTrue(Validator({"properties": {"a": False}}).validate({"a": 1}))


if __name__ == "__main__":
    unittest.main()
