"""
path_planner.py

ROS 2 node implementing A* and RRT* path planning algorithms.
Subscribes to the occupancy map and goal poses, publishes planned paths.
Supports obstacle-aware 3D path planning for drone navigation.
"""

import heapq
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

import rclpy
from rclpy.node import Node

from std_msgs.msg import Bool
from geometry_msgs.msg import PoseStamped, Point
from nav_msgs.msg import OccupancyGrid, Path
from visualization_msgs.msg import Marker, MarkerArray


@dataclass(order=True)
class AStarNode:
    """Priority queue node for A*."""
    f_cost: float
    g_cost: float = field(compare=False)
    position: Tuple[int, int, int] = field(compare=False)
    parent: Optional['AStarNode'] = field(default=None, compare=False)


class PathPlannerNode(Node):
    """Obstacle-aware path planner using A* and RRT* algorithms."""

    def __init__(self):
        super().__init__('path_planner')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('algorithm', 'astar')  # 'astar' or 'rrt_star'
        self.declare_parameter('map_resolution', 0.5)
        self.declare_parameter('safety_margin_m', 1.0)
        self.declare_parameter('max_planning_time_sec', 5.0)
        self.declare_parameter('rrt_max_iterations', 5000)
        self.declare_parameter('rrt_step_size', 1.0)
        self.declare_parameter('rrt_goal_bias', 0.1)
        self.declare_parameter('replan_on_map_update', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.algorithm = self.get_parameter('algorithm').get_parameter_value().string_value
        self.map_resolution = self.get_parameter('map_resolution').get_parameter_value().double_value
        self.safety_margin = self.get_parameter('safety_margin_m').get_parameter_value().double_value
        self.max_plan_time = self.get_parameter('max_planning_time_sec').get_parameter_value().double_value
        self.rrt_max_iter = self.get_parameter('rrt_max_iterations').get_parameter_value().integer_value
        self.rrt_step = self.get_parameter('rrt_step_size').get_parameter_value().double_value
        self.rrt_goal_bias = self.get_parameter('rrt_goal_bias').get_parameter_value().double_value
        self.replan_on_update = self.get_parameter('replan_on_map_update').get_parameter_value().bool_value

        self.get_logger().info(
            f'Path planner starting: drone_id={self.drone_id}, algorithm={self.algorithm}'
        )

        # ── State ────────────────────────────────────────────────────────
        self._occupancy_grid: Optional[OccupancyGrid] = None
        self._current_pose = PoseStamped()
        self._goal_pose: Optional[PoseStamped] = None
        self._current_path: Optional[Path] = None
        self._planning = False

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(OccupancyGrid, 'slam/map', self._map_cb, 10)
        self.create_subscription(PoseStamped, 'drone/current_pose', self._pose_cb, 10)
        self.create_subscription(PoseStamped, 'drone/goal_pose', self._goal_cb, 10)

        # ── Publishers ───────────────────────────────────────────────────
        self.path_pub = self.create_publisher(Path, 'drone/planned_path', 10)
        self.marker_pub = self.create_publisher(MarkerArray, 'drone/path_markers', 10)
        self.status_pub = self.create_publisher(Bool, 'drone/planner_active', 10)

        self.get_logger().info('Path planner node initialized')

    # ── Callbacks ────────────────────────────────────────────────────────
    def _map_cb(self, msg: OccupancyGrid):
        self._occupancy_grid = msg
        if self.replan_on_update and self._goal_pose is not None:
            self._plan_path()

    def _pose_cb(self, msg: PoseStamped):
        self._current_pose = msg

    def _goal_cb(self, msg: PoseStamped):
        self.get_logger().info(
            f'New goal received: ({msg.pose.position.x:.1f}, '
            f'{msg.pose.position.y:.1f}, {msg.pose.position.z:.1f})'
        )
        self._goal_pose = msg
        self._plan_path()

    # ── Planning ─────────────────────────────────────────────────────────
    def _plan_path(self):
        """Plan a path from current position to goal using configured algorithm."""
        if self._occupancy_grid is None:
            self.get_logger().warn('No map available for planning')
            return
        if self._goal_pose is None:
            return

        self._planning = True
        status = Bool()
        status.data = True
        self.status_pub.publish(status)

        start = (
            self._current_pose.pose.position.x,
            self._current_pose.pose.position.y,
            self._current_pose.pose.position.z,
        )
        goal = (
            self._goal_pose.pose.position.x,
            self._goal_pose.pose.position.y,
            self._goal_pose.pose.position.z,
        )

        self.get_logger().info(f'Planning path: {start} -> {goal} using {self.algorithm}')

        if self.algorithm == 'astar':
            waypoints = self._plan_astar(start, goal)
        elif self.algorithm == 'rrt_star':
            waypoints = self._plan_rrt_star(start, goal)
        else:
            self.get_logger().error(f'Unknown algorithm: {self.algorithm}')
            waypoints = None

        if waypoints:
            self._publish_path(waypoints)
            self.get_logger().info(f'Path found with {len(waypoints)} waypoints')
        else:
            self.get_logger().warn('No path found')

        self._planning = False
        status.data = False
        self.status_pub.publish(status)

    def _plan_astar(
        self, start: Tuple[float, float, float], goal: Tuple[float, float, float]
    ) -> Optional[List[Tuple[float, float, float]]]:
        """A* path planning on the occupancy grid."""
        res = self.map_resolution

        # Discretize
        start_grid = (
            int(start[0] / res),
            int(start[1] / res),
            int(start[2] / res),
        )
        goal_grid = (
            int(goal[0] / res),
            int(goal[1] / res),
            int(goal[2] / res),
        )

        def heuristic(a, b):
            return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))

        open_set = []
        start_node = AStarNode(
            f_cost=heuristic(start_grid, goal_grid),
            g_cost=0.0,
            position=start_grid,
        )
        heapq.heappush(open_set, start_node)
        closed_set = set()
        best_g = {start_grid: 0.0}

        # 26-connected 3D neighborhood
        neighbors_3d = []
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                for dz in [-1, 0, 1]:
                    if dx == 0 and dy == 0 and dz == 0:
                        continue
                    neighbors_3d.append((dx, dy, dz))

        iterations = 0
        max_iterations = 50000

        while open_set and iterations < max_iterations:
            iterations += 1
            current = heapq.heappop(open_set)

            if current.position == goal_grid:
                # Reconstruct path
                path = []
                node = current
                while node is not None:
                    wx = node.position[0] * res
                    wy = node.position[1] * res
                    wz = node.position[2] * res
                    path.append((wx, wy, wz))
                    node = node.parent
                path.reverse()
                return path

            if current.position in closed_set:
                continue
            closed_set.add(current.position)

            for dx, dy, dz in neighbors_3d:
                nx = current.position[0] + dx
                ny = current.position[1] + dy
                nz = current.position[2] + dz
                neighbor_pos = (nx, ny, nz)

                if neighbor_pos in closed_set:
                    continue

                if self._is_occupied(nx * res, ny * res):
                    continue

                if nz < 0:  # Don't go underground
                    continue

                move_cost = math.sqrt(dx * dx + dy * dy + dz * dz) * res
                new_g = current.g_cost + move_cost

                if neighbor_pos in best_g and new_g >= best_g[neighbor_pos]:
                    continue

                best_g[neighbor_pos] = new_g
                h = heuristic(neighbor_pos, goal_grid)
                neighbor_node = AStarNode(
                    f_cost=new_g + h,
                    g_cost=new_g,
                    position=neighbor_pos,
                    parent=current,
                )
                heapq.heappush(open_set, neighbor_node)

        self.get_logger().warn(f'A* exhausted after {iterations} iterations')
        return None

    def _plan_rrt_star(
        self, start: Tuple[float, float, float], goal: Tuple[float, float, float]
    ) -> Optional[List[Tuple[float, float, float]]]:
        """RRT* path planning with rewiring."""
        nodes = [start]
        parents = {0: -1}
        costs = {0: 0.0}

        goal_threshold = self.rrt_step * 2.0

        # Determine search bounds
        min_x = min(start[0], goal[0]) - 20.0
        max_x = max(start[0], goal[0]) + 20.0
        min_y = min(start[1], goal[1]) - 20.0
        max_y = max(start[1], goal[1]) + 20.0
        min_z = max(0.5, min(start[2], goal[2]) - 5.0)
        max_z = max(start[2], goal[2]) + 5.0

        best_goal_idx = -1
        best_goal_cost = float('inf')

        for iteration in range(self.rrt_max_iter):
            # Sample random point (with goal bias)
            if random.random() < self.rrt_goal_bias:
                sample = goal
            else:
                sample = (
                    random.uniform(min_x, max_x),
                    random.uniform(min_y, max_y),
                    random.uniform(min_z, max_z),
                )

            # Find nearest node
            nearest_idx = self._nearest_node(nodes, sample)
            nearest = nodes[nearest_idx]

            # Steer toward sample
            new_point = self._steer(nearest, sample, self.rrt_step)

            # Check collision
            if self._is_occupied(new_point[0], new_point[1]):
                continue

            # Find nearby nodes for rewiring
            new_idx = len(nodes)
            nodes.append(new_point)

            dist = self._dist_3d(nearest, new_point)
            new_cost = costs[nearest_idx] + dist
            parents[new_idx] = nearest_idx
            costs[new_idx] = new_cost

            # RRT* rewiring: check if nearby nodes benefit from new node
            search_radius = min(self.rrt_step * 3.0, 10.0)
            for i in range(len(nodes) - 1):
                d = self._dist_3d(nodes[i], new_point)
                if d < search_radius:
                    potential_cost = new_cost + d
                    if potential_cost < costs.get(i, float('inf')):
                        # Rewire only if collision-free
                        costs[i] = potential_cost
                        parents[i] = new_idx

            # Check if we reached the goal
            goal_dist = self._dist_3d(new_point, goal)
            if goal_dist < goal_threshold and new_cost + goal_dist < best_goal_cost:
                best_goal_idx = new_idx
                best_goal_cost = new_cost + goal_dist

        if best_goal_idx < 0:
            return None

        # Reconstruct path
        path = [goal]
        idx = best_goal_idx
        while idx >= 0:
            path.append(nodes[idx])
            idx = parents.get(idx, -1)
        path.reverse()

        return path

    # ── Utility ──────────────────────────────────────────────────────────
    def _is_occupied(self, wx: float, wy: float) -> bool:
        """Check if a world coordinate is occupied in the occupancy grid."""
        if self._occupancy_grid is None:
            return False

        grid = self._occupancy_grid
        ox = grid.info.origin.position.x
        oy = grid.info.origin.position.y
        res = grid.info.resolution

        gx = int((wx - ox) / res)
        gy = int((wy - oy) / res)

        if gx < 0 or gx >= grid.info.width or gy < 0 or gy >= grid.info.height:
            return False  # Out of map bounds = assume free

        idx = gy * grid.info.width + gx
        if idx < 0 or idx >= len(grid.data):
            return False

        # Occupied if probability > 50, also check safety margin
        margin_cells = int(self.safety_margin / res)
        for dx in range(-margin_cells, margin_cells + 1):
            for dy in range(-margin_cells, margin_cells + 1):
                cx, cy = gx + dx, gy + dy
                if 0 <= cx < grid.info.width and 0 <= cy < grid.info.height:
                    cidx = cy * grid.info.width + cx
                    if 0 <= cidx < len(grid.data) and grid.data[cidx] > 50:
                        return True
        return False

    @staticmethod
    def _dist_3d(a, b) -> float:
        return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))

    @staticmethod
    def _nearest_node(nodes, point) -> int:
        best_idx = 0
        best_dist = float('inf')
        for i, n in enumerate(nodes):
            d = sum((ni - pi) ** 2 for ni, pi in zip(n, point))
            if d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx

    @staticmethod
    def _steer(from_pt, to_pt, step_size) -> Tuple[float, float, float]:
        dx = to_pt[0] - from_pt[0]
        dy = to_pt[1] - from_pt[1]
        dz = to_pt[2] - from_pt[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist < step_size:
            return to_pt
        ratio = step_size / dist
        return (
            from_pt[0] + dx * ratio,
            from_pt[1] + dy * ratio,
            from_pt[2] + dz * ratio,
        )

    def _publish_path(self, waypoints: List[Tuple[float, float, float]]):
        """Publish the planned path as a nav_msgs/Path."""
        path_msg = Path()
        path_msg.header.stamp = self.get_clock().now().to_msg()
        path_msg.header.frame_id = 'map'

        for wx, wy, wz in waypoints:
            pose = PoseStamped()
            pose.header = path_msg.header
            pose.pose.position.x = wx
            pose.pose.position.y = wy
            pose.pose.position.z = wz
            pose.pose.orientation.w = 1.0
            path_msg.poses.append(pose)

        self._current_path = path_msg
        self.path_pub.publish(path_msg)

        # Publish visualization markers
        self._publish_markers(waypoints)

    def _publish_markers(self, waypoints: List[Tuple[float, float, float]]):
        """Publish visualization markers for the path."""
        markers = MarkerArray()

        # Path line strip
        line = Marker()
        line.header.stamp = self.get_clock().now().to_msg()
        line.header.frame_id = 'map'
        line.ns = 'planned_path'
        line.id = 0
        line.type = Marker.LINE_STRIP
        line.action = Marker.ADD
        line.scale.x = 0.1
        line.color.r = 0.0
        line.color.g = 1.0
        line.color.b = 0.0
        line.color.a = 0.8

        for wx, wy, wz in waypoints:
            p = Point()
            p.x = wx
            p.y = wy
            p.z = wz
            line.points.append(p)

        markers.markers.append(line)
        self.marker_pub.publish(markers)

    def destroy_node(self):
        self.get_logger().info('Path planner shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = PathPlannerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
