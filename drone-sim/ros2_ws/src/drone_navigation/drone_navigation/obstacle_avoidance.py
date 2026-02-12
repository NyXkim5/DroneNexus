"""
obstacle_avoidance.py

ROS 2 node for real-time obstacle avoidance using Vector Field Histogram (VFH)
and potential field methods. Subscribes to LiDAR and depth camera data,
publishes velocity commands that steer the drone away from obstacles.
"""

import math
from typing import List, Optional

import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import LaserScan, PointCloud2, Image
from geometry_msgs.msg import Twist, TwistStamped, PoseStamped, Vector3
from std_msgs.msg import Bool, Float64


class ObstacleAvoidanceNode(Node):
    """
    Real-time obstacle avoidance using VFH (Vector Field Histogram).
    Modifies velocity commands to avoid obstacles detected by LiDAR/depth.
    """

    def __init__(self):
        super().__init__('obstacle_avoidance')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('method', 'vfh')  # 'vfh' or 'potential_field'
        self.declare_parameter('update_rate', 20.0)
        self.declare_parameter('min_distance_m', 1.5)
        self.declare_parameter('critical_distance_m', 0.5)
        self.declare_parameter('max_avoidance_speed', 2.0)
        self.declare_parameter('lidar_topic', '/lidar')
        self.declare_parameter('depth_topic', '/depth_camera')
        self.declare_parameter('enabled', True)

        # VFH parameters
        self.declare_parameter('vfh_sector_count', 72)
        self.declare_parameter('vfh_threshold', 0.5)
        self.declare_parameter('vfh_wide_opening_min', 10)

        # Potential field parameters
        self.declare_parameter('repulsive_gain', 1.0)
        self.declare_parameter('attractive_gain', 0.5)
        self.declare_parameter('influence_distance_m', 5.0)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.method = self.get_parameter('method').get_parameter_value().string_value
        self.min_distance = self.get_parameter('min_distance_m').get_parameter_value().double_value
        self.critical_distance = self.get_parameter('critical_distance_m').get_parameter_value().double_value
        self.max_avoidance_speed = self.get_parameter('max_avoidance_speed').get_parameter_value().double_value
        self.enabled = self.get_parameter('enabled').get_parameter_value().bool_value

        self.vfh_sectors = self.get_parameter('vfh_sector_count').get_parameter_value().integer_value
        self.vfh_threshold = self.get_parameter('vfh_threshold').get_parameter_value().double_value
        self.vfh_wide_min = self.get_parameter('vfh_wide_opening_min').get_parameter_value().integer_value

        self.repulsive_gain = self.get_parameter('repulsive_gain').get_parameter_value().double_value
        self.attractive_gain = self.get_parameter('attractive_gain').get_parameter_value().double_value
        self.influence_dist = self.get_parameter('influence_distance_m').get_parameter_value().double_value

        self.get_logger().info(
            f'Obstacle avoidance starting: drone_id={self.drone_id}, method={self.method}'
        )

        # ── State ────────────────────────────────────────────────────────
        self._latest_scan: Optional[LaserScan] = None
        self._desired_velocity = Twist()
        self._closest_obstacle_dist = float('inf')

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        lidar_topic = self.get_parameter('lidar_topic').get_parameter_value().string_value
        self.create_subscription(LaserScan, lidar_topic, self._lidar_cb, qos_sensor)
        self.create_subscription(Twist, 'drone/cmd_vel_desired', self._desired_vel_cb, 10)
        self.create_subscription(Bool, 'obstacle_avoidance/enable', self._enable_cb, 10)

        # ── Publishers ───────────────────────────────────────────────────
        self.cmd_vel_pub = self.create_publisher(Twist, 'drone/cmd_vel', 10)
        self.obstacle_dist_pub = self.create_publisher(Float64, 'drone/closest_obstacle', 10)
        self.avoidance_active_pub = self.create_publisher(Bool, 'drone/avoidance_active', 10)

        # ── Timer ────────────────────────────────────────────────────────
        update_rate = self.get_parameter('update_rate').get_parameter_value().double_value
        self.create_timer(1.0 / update_rate, self._update)

        self.get_logger().info('Obstacle avoidance node initialized')

    # ── Callbacks ────────────────────────────────────────────────────────
    def _lidar_cb(self, msg: LaserScan):
        self._latest_scan = msg

    def _desired_vel_cb(self, msg: Twist):
        self._desired_velocity = msg

    def _enable_cb(self, msg: Bool):
        self.enabled = msg.data
        state = 'enabled' if msg.data else 'disabled'
        self.get_logger().info(f'Obstacle avoidance {state}')

    # ── Main loop ────────────────────────────────────────────────────────
    def _update(self):
        """Compute and publish obstacle-free velocity command."""
        if not self.enabled or self._latest_scan is None:
            self.cmd_vel_pub.publish(self._desired_velocity)
            return

        # Find closest obstacle
        ranges = np.array(self._latest_scan.ranges)
        valid = np.isfinite(ranges) & (ranges > self._latest_scan.range_min)
        if valid.any():
            self._closest_obstacle_dist = float(np.min(ranges[valid]))
        else:
            self._closest_obstacle_dist = float('inf')

        # Publish closest obstacle distance
        dist_msg = Float64()
        dist_msg.data = self._closest_obstacle_dist
        self.obstacle_dist_pub.publish(dist_msg)

        # Determine if avoidance is needed
        avoidance_needed = self._closest_obstacle_dist < self.min_distance
        active_msg = Bool()
        active_msg.data = avoidance_needed
        self.avoidance_active_pub.publish(active_msg)

        if not avoidance_needed:
            self.cmd_vel_pub.publish(self._desired_velocity)
            return

        # Emergency stop if critically close
        if self._closest_obstacle_dist < self.critical_distance:
            self.get_logger().warn(
                f'CRITICAL: Obstacle at {self._closest_obstacle_dist:.2f}m - stopping'
            )
            stop_cmd = Twist()
            self.cmd_vel_pub.publish(stop_cmd)
            return

        # Apply avoidance algorithm
        if self.method == 'vfh':
            cmd = self._vfh_avoidance()
        else:
            cmd = self._potential_field_avoidance()

        self.cmd_vel_pub.publish(cmd)

    def _vfh_avoidance(self) -> Twist:
        """Vector Field Histogram obstacle avoidance."""
        scan = self._latest_scan
        sector_size = 360.0 / self.vfh_sectors
        histogram = np.zeros(self.vfh_sectors)

        # Build polar histogram
        for i, r in enumerate(scan.ranges):
            if not math.isfinite(r) or r < scan.range_min:
                continue
            angle_deg = math.degrees(scan.angle_min + i * scan.angle_increment) % 360
            sector = int(angle_deg / sector_size) % self.vfh_sectors

            # Obstacle density inversely proportional to distance
            if r < self.influence_dist:
                certainty = (self.influence_dist - r) / self.influence_dist
                histogram[sector] = max(histogram[sector], certainty)

        # Find free sectors (below threshold)
        free_sectors = histogram < self.vfh_threshold

        # Find the best opening toward desired direction
        desired_angle = math.atan2(
            self._desired_velocity.linear.y,
            self._desired_velocity.linear.x,
        )
        desired_sector = int((math.degrees(desired_angle) % 360) / sector_size)

        # Search for nearest free sector to desired direction
        best_sector = self._find_best_free_sector(free_sectors, desired_sector)

        if best_sector is None:
            # No free direction found - stop
            return Twist()

        # Convert sector to velocity command
        best_angle = math.radians(best_sector * sector_size)
        desired_speed = math.sqrt(
            self._desired_velocity.linear.x ** 2 +
            self._desired_velocity.linear.y ** 2
        )
        speed = min(desired_speed, self.max_avoidance_speed)

        cmd = Twist()
        cmd.linear.x = speed * math.cos(best_angle)
        cmd.linear.y = speed * math.sin(best_angle)
        cmd.linear.z = self._desired_velocity.linear.z
        cmd.angular = self._desired_velocity.angular

        return cmd

    def _potential_field_avoidance(self) -> Twist:
        """Potential field obstacle avoidance."""
        scan = self._latest_scan

        # Attractive force toward goal (desired velocity direction)
        fx_attract = self.attractive_gain * self._desired_velocity.linear.x
        fy_attract = self.attractive_gain * self._desired_velocity.linear.y

        # Repulsive forces from obstacles
        fx_repulse = 0.0
        fy_repulse = 0.0

        for i, r in enumerate(scan.ranges):
            if not math.isfinite(r) or r < scan.range_min or r > self.influence_dist:
                continue

            angle = scan.angle_min + i * scan.angle_increment
            # Repulsive force magnitude (stronger when closer)
            magnitude = self.repulsive_gain * (1.0 / r - 1.0 / self.influence_dist) / (r * r)

            # Direction away from obstacle
            fx_repulse -= magnitude * math.cos(angle)
            fy_repulse -= magnitude * math.sin(angle)

        # Sum forces
        fx = fx_attract + fx_repulse
        fy = fy_attract + fy_repulse

        # Clamp speed
        speed = math.sqrt(fx * fx + fy * fy)
        if speed > self.max_avoidance_speed:
            scale = self.max_avoidance_speed / speed
            fx *= scale
            fy *= scale

        cmd = Twist()
        cmd.linear.x = fx
        cmd.linear.y = fy
        cmd.linear.z = self._desired_velocity.linear.z
        cmd.angular = self._desired_velocity.angular

        return cmd

    def _find_best_free_sector(
        self, free_sectors: np.ndarray, desired: int
    ) -> Optional[int]:
        """Find the free sector closest to the desired direction."""
        n = len(free_sectors)
        for offset in range(n):
            for direction in [1, -1]:
                idx = (desired + direction * offset) % n
                if free_sectors[idx]:
                    return idx
        return None

    def destroy_node(self):
        self.get_logger().info('Obstacle avoidance shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ObstacleAvoidanceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
