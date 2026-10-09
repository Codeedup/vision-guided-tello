"""Independent plant, measurement, timing and approved-boundary checks."""

from dataclasses import replace
import math
import unittest

from mock_tello.model import ApprovedGate, Config, Decision, ImagePlane, scenario_input


EPOCH = 100_000_000_000


def tracking(**changes):
    return replace(Decision(EPOCH, EPOCH, True, True, False, 5.0, 4.0), **changes)


class TestDynamics(unittest.TestCase):
    def test_one_axis_right_reduces_horizontal_error(self):
        model = ImagePlane(Config(response_tau=0))
        model.gate.accept(tracking(vertical=0), EPOCH, 0)
        model.advance(0.1, EPOCH, 0.1)
        self.assertAlmostEqual(model.x, 0.58)
        self.assertEqual(model.y, -0.4)

    def test_one_axis_up_reduces_negative_vertical_error(self):
        model = ImagePlane(Config(response_tau=0))
        model.gate.accept(tracking(lateral=0), EPOCH, 0)
        model.advance(0.1, EPOCH, 0.1)
        self.assertAlmostEqual(model.y, -0.384)
        self.assertEqual(model.x, 0.6)

    def test_lag_matches_analytic_step_response(self):
        velocity, distance = ImagePlane.response(0, 0.4, 0.15, 0.15)
        self.assertAlmostEqual(velocity, 0.4 * (1 - math.exp(-1)))
        self.assertAlmostEqual(distance, 0.4 * 0.15 * math.exp(-1))

    def test_neutral_keeps_residual_motion_then_decays(self):
        model = ImagePlane()
        model.vx = 0.4
        initial = model.x
        for _ in range(100):
            model.advance(0.01, EPOCH, 0)
        self.assertLess(model.x, initial)
        self.assertAlmostEqual(initial - model.x, 0.4 * 0.15, delta=0.0001)
        self.assertLess(model.vx, 0.001)
        self.assertEqual(model.lateral, 0)

    def test_target_motion_signs_and_no_error_clipping(self):
        model = ImagePlane(Config(initial_x=0.9, initial_y=0.9))
        model.advance(1, EPOCH, 0, target_vx=0.3, target_vy=-0.2)
        self.assertAlmostEqual(model.x, 1.2)
        self.assertAlmostEqual(model.y, 0.7)
        model.capture(EPOCH, 0)
        self.assertFalse(model.deliver(0)[0].detected)

    def test_parameter_and_step_validation(self):
        for change in ({'initial_x': 1.1}, {'gain_x': -0.1}, {'gain_y': 0},
                       {'response_tau': -0.1}, {'noise_stddev': -1},
                       {'sensor_delay': 2.1}, {'initial_y': float('nan')}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Config(**change)
        for dt in (0, -1, float('inf')):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                ImagePlane().advance(dt, EPOCH, 0)


class TestMeasurements(unittest.TestCase):
    def test_delayed_frame_retains_acquisition_time_and_values(self):
        model = ImagePlane(Config(sensor_delay=0.1))
        model.capture(EPOCH, 1)
        model.x = 0.2
        self.assertEqual(model.deliver(1.09), [])
        observation = model.deliver(1.11)[0]
        self.assertEqual(observation.stamp_ns, EPOCH)
        self.assertEqual(observation.error_x, 0.6)
        self.assertEqual(observation.tracked_hands, 1)

    def test_delay_queue_orders_frames_and_is_bounded(self):
        model = ImagePlane(Config(sensor_delay=2))
        for index in range(600):
            model.capture(EPOCH + index, index / 1000)
        self.assertEqual(len(model.pending), 512)
        delivered = model.deliver(5)
        self.assertEqual(len(delivered), 512)
        self.assertEqual([o.stamp_ns for o in delivered],
                         sorted(o.stamp_ns for o in delivered))

    def test_seeded_noise_is_repeatable_and_changes_measurement(self):
        a, b = (ImagePlane(Config(noise_stddev=0.01, seed=9)) for _ in range(2))
        for index in range(10):
            a.capture(EPOCH + index, index)
            b.capture(EPOCH + index, index)
            self.assertEqual(a.deliver(index), b.deliver(index))
        c = ImagePlane(Config(noise_stddev=0.01, seed=9))
        c.capture(EPOCH, 0)
        self.assertNotEqual(c.deliver(0)[0].error_x, c.x)

    def test_missing_ambiguous_and_silence_are_distinct(self):
        model = ImagePlane()
        model.capture(EPOCH, 0, 'missing')
        self.assertEqual(model.deliver(0)[0].tracked_hands, 0)
        model.capture(EPOCH + 1, 0, 'ambiguous')
        observation = model.deliver(0)[0]
        self.assertTrue(observation.detected)
        self.assertEqual(observation.tracked_hands, 2)
        model.capture(EPOCH + 2, 0, 'silence')
        self.assertEqual(model.deliver(0), [])

    def test_loss_scenario_recovers_sensor_after_fault_window(self):
        self.assertEqual(scenario_input('loss', 3.1, 3, 2.5)[2], 'missing')
        self.assertEqual(scenario_input('loss', 6, 3, 2.5)[2], 'normal')


class TestApprovedBoundary(unittest.TestCase):
    def setUp(self):
        self.gate = ApprovedGate()

    def test_no_decision_and_disabled_authority_are_neutral(self):
        self.assertEqual(self.gate.command(EPOCH, 0), (0, 0))
        self.gate.accept(Decision(EPOCH), EPOCH, 0)
        self.assertEqual(self.gate.command(EPOCH, 0), (0, 0))
        self.assertEqual(self.gate.mode, 'YIELDED')

    def test_fresh_bounded_authorized_commands_are_used(self):
        self.assertTrue(self.gate.accept(tracking(lateral=10, vertical=-10), EPOCH, 0))
        self.assertEqual(self.gate.command(EPOCH, 0), (10, -10))

    def test_invalid_values_and_permission_combinations_fail_closed(self):
        for change in ({'lateral': float('nan')}, {'vertical': float('inf')},
                       {'lateral': 10.001}, {'vertical': -10.001},
                       {'autonomy_enabled': False}, {'request_land': True},
                       {'tracking_allowed': False}, {'stamp_ns': 0},
                       {'source_stamp_ns': 0}, {'stamp_ns': EPOCH + 1},
                       {'source_stamp_ns': EPOCH + 1}):
            with self.subTest(change=change):
                gate = ApprovedGate()
                self.assertFalse(gate.accept(tracking(**change), EPOCH, 0))
                self.assertEqual(gate.command(EPOCH, 0), (0, 0))

    def test_source_age_boundary_independent_of_fresh_heartbeat(self):
        decision = tracking(source_stamp_ns=EPOCH - 249_999_999)
        self.assertTrue(self.gate.accept(decision, EPOCH, 0))
        self.assertEqual(self.gate.command(EPOCH + 1, 0), (0, 0))
        self.assertEqual(self.gate.mode, 'SOURCE_TIMEOUT')

    def test_bad_new_command_revokes_previous_active_input(self):
        self.gate.accept(tracking(), EPOCH, 0)
        self.assertFalse(self.gate.accept(tracking(stamp_ns=EPOCH + 1, lateral=11),
                                         EPOCH + 1, 0.01))
        self.assertEqual(self.gate.command(EPOCH + 1, 0.01), (0, 0))

    def test_source_cannot_be_later_than_its_supervisor_decision(self):
        self.assertFalse(self.gate.accept(tracking(source_stamp_ns=EPOCH + 1),
                                         EPOCH + 10, 0))
        self.assertEqual(self.gate.command(EPOCH + 10, 0), (0, 0))

    def test_decision_age_boundary_independent_of_receipt(self):
        self.gate.accept(tracking(), EPOCH, 0)
        self.assertEqual(self.gate.command(EPOCH + 250_000_000, 0.001), (0, 0))
        self.assertEqual(self.gate.mode, 'DECISION_TIMEOUT')

    def test_receipt_expiry_even_with_frozen_ros_clock(self):
        self.gate.accept(tracking(), EPOCH, 1)
        self.assertEqual(self.gate.command(EPOCH, 1.25), (0, 0))
        self.assertEqual(self.gate.mode, 'RECEIPT_TIMEOUT')

    def test_backward_clocks_revoke_input(self):
        for now, steady in ((EPOCH - 1, 1), (EPOCH, 0.9)):
            with self.subTest(clock=(now, steady)):
                gate = ApprovedGate()
                gate.accept(tracking(), EPOCH, 1)
                self.assertEqual(gate.command(now, steady), (0, 0))

    def test_replay_does_not_replace_payload_or_refresh_receipt(self):
        self.gate.accept(tracking(), EPOCH, 1)
        self.assertFalse(self.gate.accept(tracking(lateral=-10), EPOCH, 1.2))
        self.assertEqual(self.gate.command(EPOCH, 1.2), (5, 4))
        self.assertEqual(self.gate.command(EPOCH, 1.25), (0, 0))

    def test_new_heartbeat_does_not_make_old_source_fresh(self):
        self.gate.accept(tracking(), EPOCH, 0)
        self.assertTrue(self.gate.accept(tracking(stamp_ns=EPOCH + 200_000_000),
                                         EPOCH + 200_000_000, 0.2))
        self.assertEqual(self.gate.command(EPOCH + 250_000_000, 0.25), (0, 0))

    def test_landing_latches_across_takeover_until_explicit_mock_reset(self):
        landing = Decision(EPOCH, autonomy_enabled=True, request_land=True)
        self.assertTrue(self.gate.accept(landing, EPOCH, 0))
        self.assertFalse(self.gate.reset_allowed(EPOCH, 0))
        self.assertTrue(self.gate.accept(tracking(stamp_ns=EPOCH + 1), EPOCH + 1, 0.01))
        self.assertEqual(self.gate.command(EPOCH + 1, 0.01), (0, 0))
        self.assertTrue(self.gate.terminal)
        self.gate.accept(Decision(EPOCH + 2), EPOCH + 2, 0.02)
        self.assertTrue(self.gate.reset_allowed(EPOCH + 2, 0.02))
        self.assertTrue(self.gate.terminal)
        self.assertFalse(self.gate.reset_allowed(EPOCH + 250_000_002, 0.02))

    def test_invalid_landing_cannot_latch_action(self):
        self.assertFalse(self.gate.accept(
            Decision(EPOCH, request_land=True), EPOCH, 0))
        self.assertFalse(self.gate.terminal)


if __name__ == '__main__':
    unittest.main()
