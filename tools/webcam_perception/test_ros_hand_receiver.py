import json
import unittest

from ros_hand_receiver import decode_observation, WebcamReceiver
from unittest.mock import Mock


class TestObservationDecoder(unittest.TestCase):
    NOW_NS = 1_800_000_000_000_000_000

    def observation(self):
        return {
            "capture_time_ns": self.NOW_NS,
            "detected": True,
            "error_x": 0.2,
            "error_y": -0.1,
            "tracked_hands": 1,
        }

    def decode(self, observation):
        packet = json.dumps(observation).encode("utf-8")
        return decode_observation(packet, self.NOW_NS)

    def test_freshness_boundary(self):
        for age_ns, accepted in [
            (0, True),
            (249_999_999, True),
            (250_000_000, False),
            (300_000_000, False),
            (-1, False),
        ]:
            with self.subTest(age_ns=age_ns):
                observation = self.observation()
                observation["capture_time_ns"] -= age_ns

                if accepted:
                    self.decode(observation)
                else:
                    with self.assertRaises(ValueError):
                        self.decode(observation)

    def test_invalid_fields(self):
        cases = [
            ("capture_time_ns", True),
            ("detected", "true"),
            ("tracked_hands", 2),
            ("tracked_hands", -1),
            ("error_x", "0.2"),
            ("error_x", float("nan")),
            ("error_y", float("inf")),
            ("error_x", 1.01),
            ("detected", False),
        ]

        for field, value in cases:
            with self.subTest(field=field, value=value):
                observation = self.observation()
                observation[field] = value

                with self.assertRaises(ValueError):
                    self.decode(observation)

    def test_invalid_json(self):
        with self.assertRaises(ValueError):
            decode_observation(b"not JSON", self.NOW_NS)

    def test_missing_field(self):
        observation = self.observation()
        del observation["capture_time_ns"]

        with self.assertRaises(KeyError):
            self.decode(observation)


    def process_packets(self, observations, last_stamp=0, publish_age_ns=0):
        node = Mock()
        node.get_parameter.return_value.value = False
        node.get_clock.return_value.now.side_effect = [
            Mock(nanoseconds=self.NOW_NS)
            for _ in observations
        ] + [
            Mock(nanoseconds=self.NOW_NS + publish_age_ns)
        ]
        node.last_published_stamp = last_stamp

        packets = [
            (json.dumps(item).encode(), ("127.0.0.1", 5005))
            for item in observations
        ]
        node.receiver.recvfrom.side_effect = (
            packets + [BlockingIOError()]
        )

        WebcamReceiver.receive_packets(node)
        return node

    def test_valid_packet_is_published(self):
        node = self.process_packets([self.observation()])
        node.publisher.publish.assert_called_once()

        message = node.publisher.publish.call_args.args[0]
        stamp_ns = (
            message.header.stamp.sec * 1_000_000_000
            + message.header.stamp.nanosec
        )
        self.assertEqual(stamp_ns, self.NOW_NS)
        self.assertEqual(node.last_published_stamp, self.NOW_NS)

    def test_rejected_packet_does_not_block_valid_packet(self):
        invalid = self.observation()
        invalid["tracked_hands"] = 2

        node = self.process_packets([invalid, self.observation()])
        node.publisher.publish.assert_called_once()

    def test_duplicate_and_older_packets_are_not_published(self):
        duplicate = self.observation()
        older = self.observation()
        older["capture_time_ns"] -= 1_000_000

        node = self.process_packets(
            [duplicate, older],
            last_stamp=self.NOW_NS,
        )
        node.publisher.publish.assert_not_called()

    def test_silence_does_not_publish_replacement_observation(self):
        node = self.process_packets([])
        node.publisher.publish.assert_not_called()

    def test_newest_packet_in_batch_is_selected(self):
        older = self.observation()
        older["capture_time_ns"] -= 2_000_000

        newest = self.observation()

        middle = self.observation()
        middle["capture_time_ns"] -= 1_000_000

        node = self.process_packets([older, newest, middle])
        node.publisher.publish.assert_called_once()

        message = node.publisher.publish.call_args.args[0]
        stamp_ns = (
            message.header.stamp.sec * 1_000_000_000
            + message.header.stamp.nanosec
        )
        self.assertEqual(stamp_ns, newest["capture_time_ns"])

    def test_freshness_is_rechecked_before_publication(self):
        for age_ns, accepted in [
            (249_999_999, True),
            (250_000_000, False),
        ]:
            with self.subTest(age_ns=age_ns):
                node = self.process_packets(
                    [self.observation()],
                    publish_age_ns=age_ns,
                )

                if accepted:
                    node.publisher.publish.assert_called_once()
                else:
                    node.publisher.publish.assert_not_called()
                    self.assertEqual(node.last_published_stamp, 0)

if __name__ == "__main__":
    unittest.main()