"""Minimal geometry ABI: payload codecs, validity and 1-D binding evaluation."""

from __future__ import annotations

import struct
import unittest

from oneclick2d.errors import ContractError
from oneclick2d.geometry import (
    DELTA_FORMAT,
    INDEX_FORMAT_U16,
    INDEX_FORMAT_U32,
    VERTEX_FORMAT,
    Mesh,
    Vertex,
    apply_deltas,
    check_mesh,
    decode_deltas,
    decode_indices,
    decode_vertices,
    encode_deltas,
    encode_indices,
    encode_vertices,
    grid_mesh,
    interpolate_deltas,
    signed_area,
)


class GridMeshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mesh = grid_mesh(10, 20, 80, 60, 4, 3, 1024, 1024)

    def test_grid_shape(self) -> None:
        self.assertEqual(self.mesh.vertex_count, 5 * 4)
        self.assertEqual(self.mesh.triangle_count, 4 * 3 * 2)

    def test_grid_tiles_its_bounds_exactly(self) -> None:
        total = sum(
            abs(signed_area(self.mesh.vertices[a], self.mesh.vertices[b], self.mesh.vertices[c]))
            for a, b, c in self.mesh.triangles
        ) / 2.0
        self.assertAlmostEqual(total, 80 * 60, places=4)

    def test_every_triangle_is_clockwise_in_y_down_space(self) -> None:
        for a, b, c in self.mesh.triangles:
            self.assertGreater(
                signed_area(self.mesh.vertices[a], self.mesh.vertices[b], self.mesh.vertices[c]), 0
            )

    def test_uv_stays_normalized(self) -> None:
        for vertex in self.mesh.vertices:
            self.assertGreaterEqual(vertex.u, 0.0)
            self.assertLessEqual(vertex.u, 1.0)
            self.assertGreaterEqual(vertex.v, 0.0)
            self.assertLessEqual(vertex.v, 1.0)

    def test_grid_is_deterministic(self) -> None:
        self.assertEqual(grid_mesh(10, 20, 80, 60, 4, 3, 1024, 1024).vertices, self.mesh.vertices)

    def test_index_width_follows_vertex_count(self) -> None:
        self.assertEqual(self.mesh.index_format, INDEX_FORMAT_U16)
        wide = grid_mesh(0, 0, 1000, 1000, 300, 300, 1024, 1024)
        self.assertGreater(wide.vertex_count, 0xFFFF)
        self.assertEqual(wide.index_format, INDEX_FORMAT_U32)

    def test_degenerate_grid_requests_are_rejected(self) -> None:
        for args in ((10, 20, 0, 60, 4, 3), (10, 20, 80, 60, 0, 3)):
            with self.subTest(args=args), self.assertRaises(ContractError):
                grid_mesh(*args, 1024, 1024)


class PayloadCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mesh = grid_mesh(0, 0, 40, 40, 2, 2, 256, 256)

    def test_vertex_payload_round_trip_and_stride(self) -> None:
        payload = encode_vertices(self.mesh.vertices)
        self.assertEqual(len(payload), self.mesh.vertex_count * 16)
        self.assertEqual(decode_vertices(payload), self.mesh.vertices)

    def test_vertex_payload_is_little_endian_regardless_of_host(self) -> None:
        payload = encode_vertices(self.mesh.vertices)
        self.assertEqual(payload[:4], struct.pack("<f", self.mesh.vertices[0].x))

    def test_index_payload_round_trip_and_stride(self) -> None:
        payload, index_format = encode_indices(self.mesh.triangles, self.mesh.vertex_count)
        self.assertEqual(index_format, INDEX_FORMAT_U16)
        self.assertEqual(len(payload), self.mesh.triangle_count * 3 * 2)
        self.assertEqual(decode_indices(payload, index_format, self.mesh.vertex_count), self.mesh.triangles)

    def test_delta_payload_round_trip(self) -> None:
        deltas = [(float(index), float(-index)) for index in range(self.mesh.vertex_count)]
        payload = encode_deltas(deltas, self.mesh.vertex_count)
        self.assertEqual(len(payload), self.mesh.vertex_count * 8)
        self.assertEqual(decode_deltas(payload, self.mesh.vertex_count), tuple(deltas))

    def test_format_identifiers_are_the_declared_constants(self) -> None:
        self.assertEqual(VERTEX_FORMAT, "oc2d.mesh.xyuv.f32le.v1")
        self.assertEqual(DELTA_FORMAT, "oc2d.delta.xy.f32le.v1")

    def test_lengths_are_verified_before_elements_are_read(self) -> None:
        payload = encode_vertices(self.mesh.vertices)
        with self.assertRaises(ContractError):
            decode_vertices(payload[:-3])
        indices, index_format = encode_indices(self.mesh.triangles, self.mesh.vertex_count)
        with self.assertRaises(ContractError):
            decode_indices(indices[:-3], index_format, self.mesh.vertex_count)

    def test_unknown_index_format_is_rejected(self) -> None:
        indices, _ = encode_indices(self.mesh.triangles, self.mesh.vertex_count)
        with self.assertRaises(ContractError):
            decode_indices(indices, "oc2d.indices.bogus.v1", self.mesh.vertex_count)

    def test_out_of_range_index_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            encode_indices([(0, 1, 999)], self.mesh.vertex_count)

    def test_repeated_vertex_in_a_triangle_is_rejected_on_decode(self) -> None:
        with self.assertRaises(ContractError):
            decode_indices(struct.pack("<HHH", 0, 0, 1), INDEX_FORMAT_U16, 4)

    def test_non_finite_values_are_rejected(self) -> None:
        with self.assertRaises(ContractError):
            encode_vertices([Vertex(float("inf"), 0, 0, 0), Vertex(1, 0, 0, 0), Vertex(0, 1, 0, 0)])
        with self.assertRaises(ContractError):
            encode_deltas([(float("nan"), 0.0)] * self.mesh.vertex_count, self.mesh.vertex_count)

    def test_uv_outside_the_unit_square_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            encode_vertices([Vertex(0, 0, 1.5, 0), Vertex(1, 0, 0, 0), Vertex(0, 1, 0, 0)])

    def test_delta_count_must_match_the_target_mesh(self) -> None:
        with self.assertRaises(ContractError):
            encode_deltas([(0.0, 0.0)], self.mesh.vertex_count)


class MeshValidityTests(unittest.TestCase):
    def test_counter_clockwise_winding_is_rejected(self) -> None:
        mesh = Mesh((Vertex(0, 0, 0, 0), Vertex(10, 0, 0, 0), Vertex(0, 10, 0, 0)), ((0, 2, 1),))
        with self.assertRaises(ContractError):
            check_mesh(mesh)

    def test_collinear_triangle_is_rejected(self) -> None:
        mesh = Mesh((Vertex(0, 0, 0, 0), Vertex(5, 5, 0, 0), Vertex(10, 10, 0, 0)), ((0, 1, 2),))
        with self.assertRaises(ContractError):
            check_mesh(mesh)

    def test_duplicate_triangle_is_rejected(self) -> None:
        base = grid_mesh(0, 0, 10, 10, 1, 1, 64, 64)
        mesh = Mesh(base.vertices, (base.triangles[0], base.triangles[0]))
        with self.assertRaises(ContractError):
            check_mesh(mesh)


class BindingEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.count = 4
        self.zeros = [(0.0, 0.0)] * self.count
        self.tens = [(10.0, -10.0)] * self.count
        self.samples = [(-15.0, self.zeros), (0.0, self.zeros), (15.0, self.tens)]

    def test_linear_interpolation_between_samples(self) -> None:
        result = interpolate_deltas(self.samples, 7.5, self.count)
        self.assertAlmostEqual(result[0][0], 5.0)
        self.assertAlmostEqual(result[0][1], -5.0)

    def test_extrapolation_clamps_at_both_ends(self) -> None:
        self.assertEqual(interpolate_deltas(self.samples, -99.0, self.count)[0], (0.0, 0.0))
        self.assertEqual(interpolate_deltas(self.samples, 99.0, self.count)[0], (10.0, -10.0))

    def test_exact_knot_returns_that_sample(self) -> None:
        self.assertEqual(interpolate_deltas(self.samples, 15.0, self.count)[0], (10.0, -10.0))

    def test_samples_must_strictly_increase(self) -> None:
        with self.assertRaises(ContractError):
            interpolate_deltas([(1.0, self.zeros), (1.0, self.tens)], 1.0, self.count)

    def test_at_least_two_samples_are_required(self) -> None:
        with self.assertRaises(ContractError):
            interpolate_deltas([(1.0, self.zeros)], 1.0, self.count)

    def test_apply_deltas_moves_positions_and_keeps_uv(self) -> None:
        mesh = grid_mesh(0, 0, 10, 10, 1, 1, 64, 64)
        deltas = [(float(index), 0.0) for index in range(mesh.vertex_count)]
        moved = apply_deltas(mesh, deltas)
        self.assertEqual([v.u for v in moved.vertices], [v.u for v in mesh.vertices])
        self.assertAlmostEqual(moved.vertices[3].x, mesh.vertices[3].x + 3.0)

    def test_apply_deltas_rejects_a_count_mismatch(self) -> None:
        mesh = grid_mesh(0, 0, 10, 10, 1, 1, 64, 64)
        with self.assertRaises(ContractError):
            apply_deltas(mesh, [(0.0, 0.0)])


if __name__ == "__main__":
    unittest.main()
