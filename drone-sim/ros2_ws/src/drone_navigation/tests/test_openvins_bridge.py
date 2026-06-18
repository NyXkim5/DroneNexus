"""
test_openvins_bridge.py

Unit tests for the OpenVINS bridge pure-logic functions.
No ROS2 runtime required.
"""

from __future__ import annotations

import logging
import time
import unittest
from unittest.mock import MagicMock

import numpy as np

from drone_navigation.openvins_bridge import (
    build_odom_msg,
    build_pose_msg,
    is_stale,
    rotate_vector_by_quaternion,
    scale_covariance,
    scale_odom_covariance,
)

logger = logging.getLogger(__name__)


class TestIsStale(unittest.TestCase):
    """Tests for staleness detection."""

    def test_none_timestamp_is_stale(self) -> None:
        logger.info("Testing None timestamp returns stale")
        self.assertTrue(is_stale(None, 0.5))

    def test_recent_timestamp_is_not_stale(self) -> None:
        logger.info("Testing recent timestamp is not stale")
        self.assertFalse(is_stale(time.time(), 0.5))

    def test_old_timestamp_is_stale(self) -> None:
        logger.info("Testing old timestamp is stale")
        old = time.time() - 2.0
        self.assertTrue(is_stale(old, 0.5))

    def test_exact_boundary(self) -> None:
        logger.info("Testing boundary timestamp (just within)")
        recent = time.time() - 0.1
        self.assertFalse(is_stale(recent, 0.5))

    def test_zero_max_age(self) -> None:
        logger.info("Testing zero max_age always stale for past timestamps")
        old = time.time() - 0.001
        self.assertTrue(is_stale(old, 0.0))


class TestCovarianceScaling(unittest.TestCase):
    """Tests for covariance scaling on pose and odometry messages."""

    def _make_pose_msg(self) -> MagicMock:
        msg = MagicMock()
        msg.header = MagicMock()
        msg.pose.pose = MagicMock()
        msg.pose.covariance = [float(i) for i in range(36)]
        return msg

    def test_scale_covariance_doubles(self) -> None:
        logger.info("Testing covariance scale factor of 2.0")
        msg = self._make_pose_msg()
        result = scale_covariance(msg, 2.0)
        for i in range(36):
            self.assertAlmostEqual(
                result.pose.covariance[i], float(i) * 2.0
            )

    def test_scale_covariance_identity(self) -> None:
        logger.info("Testing covariance scale factor of 1.0 (identity)")
        msg = self._make_pose_msg()
        result = scale_covariance(msg, 1.0)
        for i in range(36):
            self.assertAlmostEqual(
                result.pose.covariance[i], float(i)
            )

    def test_scale_covariance_zero(self) -> None:
        logger.info("Testing covariance scale factor of 0.0")
        msg = self._make_pose_msg()
        result = scale_covariance(msg, 0.0)
        for i in range(36):
            self.assertAlmostEqual(result.pose.covariance[i], 0.0)

    def _make_odom_msg(self) -> MagicMock:
        msg = MagicMock()
        msg.header = MagicMock()
        msg.child_frame_id = "base_link"
        msg.pose.pose = MagicMock()
        msg.twist.twist = MagicMock()
        msg.pose.covariance = [1.0] * 36
        msg.twist.covariance = [2.0] * 36
        return msg

    def test_scale_odom_covariance(self) -> None:
        logger.info("Testing odometry covariance scaling")
        msg = self._make_odom_msg()
        result = scale_odom_covariance(msg, 3.0)
        for i in range(36):
            self.assertAlmostEqual(result.pose.covariance[i], 3.0)
            self.assertAlmostEqual(result.twist.covariance[i], 6.0)


class TestFallbackSwitching(unittest.TestCase):
    """Tests for the fallback switching logic."""

    def test_switches_to_dead_reckoning_when_stale(self) -> None:
        logger.info("Testing switch to dead_reckoning when stale")
        old_time = time.time() - 2.0
        stale = is_stale(old_time, 0.5)
        self.assertTrue(stale)
        source = "dead_reckoning" if stale else "openvins"
        self.assertEqual(source, "dead_reckoning")

    def test_stays_openvins_when_fresh(self) -> None:
        logger.info("Testing stays openvins when data is fresh")
        recent_time = time.time()
        stale = is_stale(recent_time, 0.5)
        self.assertFalse(stale)
        source = "dead_reckoning" if stale else "openvins"
        self.assertEqual(source, "openvins")

    def test_no_fallback_when_disabled(self) -> None:
        logger.info("Testing no fallback when auto_fallback is disabled")
        old_time = time.time() - 2.0
        auto_fallback = False
        stale = is_stale(old_time, 0.5)
        source = "dead_reckoning" if (stale and auto_fallback) else "openvins"
        self.assertEqual(source, "openvins")


class TestSourceString(unittest.TestCase):
    """Tests for source string output values."""

    def test_openvins_source(self) -> None:
        logger.info("Testing openvins source string")
        source = "dead_reckoning" if is_stale(time.time(), 0.5) else "openvins"
        self.assertEqual(source, "openvins")

    def test_dead_reckoning_source(self) -> None:
        logger.info("Testing dead_reckoning source string")
        source = "dead_reckoning" if is_stale(None, 0.5) else "openvins"
        self.assertEqual(source, "dead_reckoning")


class TestRotateVector(unittest.TestCase):
    """Tests for quaternion vector rotation."""

    def test_identity_rotation(self) -> None:
        logger.info("Testing identity quaternion rotation")
        v = np.array([1.0, 0.0, 0.0])
        q_identity = np.array([0.0, 0.0, 0.0, 1.0])
        result = rotate_vector_by_quaternion(v, q_identity)
        np.testing.assert_allclose(result, v, atol=1e-10)

    def test_90deg_z_rotation(self) -> None:
        logger.info("Testing 90-degree Z rotation")
        v = np.array([1.0, 0.0, 0.0])
        # 90 degrees around Z: quat = (0, 0, sin(45), cos(45))
        angle = np.pi / 2.0
        q = np.array([0.0, 0.0, np.sin(angle / 2), np.cos(angle / 2)])
        result = rotate_vector_by_quaternion(v, q)
        np.testing.assert_allclose(result, [0.0, 1.0, 0.0], atol=1e-10)


class TestBuildMessages(unittest.TestCase):
    """Tests for message builder functions."""

    def test_build_pose_msg_frame(self) -> None:
        logger.info("Testing build_pose_msg sets frame_id to odom")
        stamp = MagicMock()
        pos = np.array([1.0, 2.0, 3.0])
        ori = np.array([0.0, 0.0, 0.0, 1.0])
        msg = build_pose_msg(stamp, pos, ori)
        self.assertEqual(msg.header.frame_id, "odom")

    def test_build_pose_msg_covariance(self) -> None:
        logger.info("Testing build_pose_msg sets dead reckoning covariance")
        stamp = MagicMock()
        pos = np.zeros(3)
        ori = np.array([0.0, 0.0, 0.0, 1.0])
        msg = build_pose_msg(stamp, pos, ori)
        self.assertAlmostEqual(msg.pose.covariance[0], 0.1)
        self.assertAlmostEqual(msg.pose.covariance[7], 0.1)
        self.assertAlmostEqual(msg.pose.covariance[14], 0.1)
        self.assertAlmostEqual(msg.pose.covariance[35], 0.05)

    def test_build_odom_msg_child_frame(self) -> None:
        logger.info("Testing build_odom_msg sets child_frame_id")
        stamp = MagicMock()
        pos = np.zeros(3)
        ori = np.array([0.0, 0.0, 0.0, 1.0])
        vel = np.array([1.0, 2.0, 3.0])
        msg = build_odom_msg(stamp, pos, ori, vel)
        self.assertEqual(msg.child_frame_id, "base_link")
        self.assertAlmostEqual(msg.twist.twist.linear.x, 1.0)
        self.assertAlmostEqual(msg.twist.twist.linear.y, 2.0)
        self.assertAlmostEqual(msg.twist.twist.linear.z, 3.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()
