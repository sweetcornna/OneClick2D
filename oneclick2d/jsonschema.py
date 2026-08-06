"""Standard-library JSON Schema draft 2020-12 validator (bounded subset).

``docs/PACKAGE_CONFORMANCE.md`` §8 requires a standards-compliant schema tool for
wire-shape validation. This implements the keyword subset the repository's own
schemas use, and *fails closed on any keyword it does not implement* rather than
silently ignoring it — an ignored keyword would turn a constraint into a
rubber stamp.

Supported: ``$ref``/``$defs`` (local pointers), ``type``, ``const``, ``enum``,
``properties``, ``required``, ``additionalProperties``, ``patternProperties``,
``propertyNames``, ``minProperties``/``maxProperties``, ``items``,
``prefixItems``, ``minItems``/``maxItems``, ``uniqueItems``, ``contains``,
``minimum``/``maximum``/``exclusiveMinimum``/``exclusiveMaximum``,
``multipleOf``, ``minLength``/``maxLength``, ``pattern``, ``format`` (annotative),
``allOf``/``anyOf``/``oneOf``/``not``, and ``if``/``then``/``else``.
"""

from __future__ import annotations

import math
import re
from typing import Any, Final

from .errors import ContractError

_ANNOTATIONS: Final[frozenset[str]] = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "$defs",
        "title",
        "description",
        "examples",
        "default",
        "deprecated",
        "readOnly",
        "writeOnly",
        "format",
    }
)
_SUPPORTED: Final[frozenset[str]] = frozenset(
    {
        "$ref",
        "type",
        "const",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "patternProperties",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "uniqueItems",
        "contains",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
    }
)
MAX_DEPTH: Final[int] = 128


class SchemaError(ContractError):
    """The schema itself is unusable or uses an unimplemented keyword."""


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "number":
        return not isinstance(value, bool) and isinstance(value, (int, float))
    if expected == "integer":
        return _is_integer(value)
    raise SchemaError(f"unsupported JSON Schema type: {expected}")


def _equal(left: Any, right: Any) -> bool:
    """JSON equality: 1 and 1.0 are equal, but booleans never equal numbers."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right, strict=True))
    if type(left) is not type(right):
        return False
    return bool(left == right)


class Validator:
    """A compiled schema that reports every violation with a JSON pointer."""

    def __init__(self, schema: dict[str, Any]) -> None:
        if not isinstance(schema, dict):
            raise SchemaError("schema must be a JSON object")
        self.schema = schema
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._check_keywords(schema, "#")

    def _check_keywords(self, schema: Any, pointer: str) -> None:
        """Reject unimplemented keywords up front so nothing is silently ignored."""
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise SchemaError(f"{pointer}: schema must be an object or boolean")
        for keyword, value in schema.items():
            if keyword in _ANNOTATIONS:
                continue
            if keyword not in _SUPPORTED:
                raise SchemaError(f"{pointer}: unsupported JSON Schema keyword: {keyword}")
            if keyword in ("properties", "patternProperties"):
                if not isinstance(value, dict):
                    raise SchemaError(f"{pointer}/{keyword}: must be an object")
                for name, subschema in value.items():
                    self._check_keywords(subschema, f"{pointer}/{keyword}/{name}")
            elif keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
                if not isinstance(value, list) or not value:
                    raise SchemaError(f"{pointer}/{keyword}: must be a non-empty array")
                for index, subschema in enumerate(value):
                    self._check_keywords(subschema, f"{pointer}/{keyword}/{index}")
            elif keyword in (
                "items",
                "additionalProperties",
                "propertyNames",
                "contains",
                "not",
                "if",
                "then",
                "else",
            ):
                self._check_keywords(value, f"{pointer}/{keyword}")
        defs = schema.get("$defs")
        if isinstance(defs, dict):
            for name, subschema in defs.items():
                self._check_keywords(subschema, f"{pointer}/$defs/{name}")

    def _resolve(self, reference: str, pointer: str) -> Any:
        if not reference.startswith("#"):
            raise SchemaError(f"{pointer}: only local $ref pointers are supported")
        target: Any = self.schema
        for token in reference.lstrip("#").split("/"):
            if not token:
                continue
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or token not in target:
                raise SchemaError(f"{pointer}: $ref does not resolve: {reference}")
            target = target[token]
        return target

    def _pattern(self, pattern: str) -> re.Pattern[str]:
        compiled = self._patterns.get(pattern)
        if compiled is None:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise SchemaError(f"invalid regular expression in schema: {pattern}") from exc
            self._patterns[pattern] = compiled
        return compiled

    def validate(self, instance: Any) -> list[str]:
        """Return a list of human-readable violations; empty means valid."""
        errors: list[str] = []
        self._validate(instance, self.schema, "", errors, 0)
        return errors

    def check(self, instance: Any, *, label: str = "document") -> None:
        """Raise ``ContractError`` when ``instance`` violates the schema."""
        errors = self.validate(instance)
        if errors:
            raise ContractError(f"{label} failed schema validation: {errors[0]}")

    def _validate(self, instance: Any, schema: Any, path: str, errors: list[str], depth: int) -> None:
        if depth > MAX_DEPTH:
            raise SchemaError("schema evaluation depth limit exceeded")
        if schema is True or schema == {}:
            return
        if schema is False:
            errors.append(f"{path or '/'}: no value is permitted here")
            return
        if not isinstance(schema, dict):
            raise SchemaError(f"{path or '/'}: schema must be an object or boolean")

        if "$ref" in schema:
            resolved = self._resolve(str(schema["$ref"]), path or "#")
            self._validate(instance, resolved, path, errors, depth + 1)
            # Sibling keywords still apply in 2020-12.

        location = path or "/"
        if "type" in schema:
            expected = schema["type"]
            options = expected if isinstance(expected, list) else [expected]
            if not any(_type_matches(instance, str(option)) for option in options):
                errors.append(f"{location}: expected type {expected}")
                return
        if "const" in schema and not _equal(instance, schema["const"]):
            errors.append(f"{location}: value must equal the declared const")
        if "enum" in schema:
            options = schema["enum"]
            if not isinstance(options, list):
                raise SchemaError(f"{location}: enum must be an array")
            if not any(_equal(instance, option) for option in options):
                errors.append(f"{location}: value is not one of the permitted enum members")

        for keyword, combinator in (("allOf", "all"), ("anyOf", "any"), ("oneOf", "one")):
            if keyword not in schema:
                continue
            subschemas = schema[keyword]
            outcomes = []
            for subschema in subschemas:
                nested: list[str] = []
                self._validate(instance, subschema, path, nested, depth + 1)
                outcomes.append(nested)
            passing = sum(1 for nested in outcomes if not nested)
            if combinator == "all" and passing != len(outcomes):
                first = next(nested for nested in outcomes if nested)
                errors.append(f"{location}: allOf member failed: {first[0]}")
            elif combinator == "any" and passing == 0:
                errors.append(f"{location}: no anyOf member matched")
            elif combinator == "one" and passing != 1:
                errors.append(f"{location}: exactly one oneOf member must match, matched {passing}")

        if "not" in schema:
            nested = []
            self._validate(instance, schema["not"], path, nested, depth + 1)
            if not nested:
                errors.append(f"{location}: value must not match the negated schema")

        if "if" in schema:
            nested = []
            self._validate(instance, schema["if"], path, nested, depth + 1)
            branch = "then" if not nested else "else"
            if branch in schema:
                self._validate(instance, schema[branch], path, errors, depth + 1)

        if isinstance(instance, dict):
            self._validate_object(instance, schema, path, errors, depth)
        elif isinstance(instance, list):
            self._validate_array(instance, schema, path, errors, depth)
        elif isinstance(instance, str):
            self._validate_string(instance, schema, location, errors)
        elif not isinstance(instance, bool) and isinstance(instance, (int, float)):
            self._validate_number(instance, schema, location, errors)

    def _validate_object(
        self, instance: dict[str, Any], schema: dict[str, Any], path: str, errors: list[str], depth: int
    ) -> None:
        location = path or "/"
        required = schema.get("required")
        if required is not None:
            if not isinstance(required, list):
                raise SchemaError(f"{location}: required must be an array")
            for name in required:
                if name not in instance:
                    errors.append(f"{location}: missing required property {name}")
        minimum = schema.get("minProperties")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{location}: fewer than {minimum} properties")
        maximum = schema.get("maxProperties")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{location}: more than {maximum} properties")

        properties = schema.get("properties") or {}
        pattern_properties = schema.get("patternProperties") or {}
        names_schema = schema.get("propertyNames")
        additional = schema.get("additionalProperties")

        for name, value in instance.items():
            child = f"{path}/{name}"
            matched = False
            if name in properties:
                matched = True
                self._validate(value, properties[name], child, errors, depth + 1)
            for pattern, subschema in pattern_properties.items():
                if self._pattern(pattern).search(name):
                    matched = True
                    self._validate(value, subschema, child, errors, depth + 1)
            if names_schema is not None:
                self._validate(name, names_schema, f"{child}<name>", errors, depth + 1)
            if not matched and additional is not None:
                if additional is False:
                    errors.append(f"{child}: additional property is not permitted")
                else:
                    self._validate(value, additional, child, errors, depth + 1)

    def _validate_array(
        self, instance: list[Any], schema: dict[str, Any], path: str, errors: list[str], depth: int
    ) -> None:
        location = path or "/"
        prefix = schema.get("prefixItems") or []
        for index, subschema in enumerate(prefix):
            if index < len(instance):
                self._validate(instance[index], subschema, f"{path}/{index}", errors, depth + 1)
        items = schema.get("items")
        if items is not None:
            for index in range(len(prefix), len(instance)):
                self._validate(instance[index], items, f"{path}/{index}", errors, depth + 1)
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{location}: fewer than {minimum} items")
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{location}: more than {maximum} items")
        if schema.get("uniqueItems") is True:
            for outer in range(len(instance)):
                for inner in range(outer + 1, len(instance)):
                    if _equal(instance[outer], instance[inner]):
                        errors.append(f"{location}: items must be unique")
                        break
                else:
                    continue
                break
        contains = schema.get("contains")
        if contains is not None:
            for item in instance:
                nested: list[str] = []
                self._validate(item, contains, path, nested, depth + 1)
                if not nested:
                    break
            else:
                errors.append(f"{location}: no item matched the contains schema")

    def _validate_string(self, instance: str, schema: dict[str, Any], location: str, errors: list[str]) -> None:
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{location}: shorter than {minimum} characters")
        maximum = schema.get("maxLength")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{location}: longer than {maximum} characters")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not self._pattern(pattern).search(instance):
            errors.append(f"{location}: does not match the required pattern")

    def _validate_number(
        self, instance: int | float, schema: dict[str, Any], location: str, errors: list[str]
    ) -> None:
        value = float(instance)
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < float(minimum):
            errors.append(f"{location}: below the minimum of {minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > float(maximum):
            errors.append(f"{location}: above the maximum of {maximum}")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if (
            isinstance(exclusive_minimum, (int, float))
            and not isinstance(exclusive_minimum, bool)
            and value <= float(exclusive_minimum)
        ):
            errors.append(f"{location}: not above the exclusive minimum of {exclusive_minimum}")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if (
            isinstance(exclusive_maximum, (int, float))
            and not isinstance(exclusive_maximum, bool)
            and value >= float(exclusive_maximum)
        ):
            errors.append(f"{location}: not below the exclusive maximum of {exclusive_maximum}")
        multiple_of = schema.get("multipleOf")
        if isinstance(multiple_of, (int, float)) and not isinstance(multiple_of, bool):
            divisor = float(multiple_of)
            if divisor <= 0:
                raise SchemaError(f"{location}: multipleOf must be positive")
            quotient = value / divisor
            if not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=1e-9):
                errors.append(f"{location}: not a multiple of {multiple_of}")


def load_validator(path: Any) -> Validator:
    """Compile the schema stored at ``path``."""
    from pathlib import Path

    from .strict_json import loads_strict

    schema = loads_strict(Path(path).read_bytes())
    if not isinstance(schema, dict):
        raise SchemaError("schema document must be a JSON object")
    return Validator(schema)
