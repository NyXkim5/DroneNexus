"""
websocket_bridge.py

Python WebSocket server that bridges ROS 2 topics to the Electron app.
Subscribes to drone state, sensor data, and navigation topics;
forwards them as JSON over WebSocket. Accepts commands from the Electron
app and publishes them to ROS 2 topics/services.
"""

import asyncio
import json
import signal
import threading
from typing import Dict, Set

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import NavSatFix, BatteryState, Imu

from drone_interfaces.msg import DroneState
from drone_interfaces.srv import SetFlightMode, UpdatePID

try:
    import websockets
except ImportError:
    print("ERROR: websockets package not installed. Run: pip3 install websockets")
    raise


class WebSocketBridgeNode(Node):
    """ROS 2 node that bridges topics to WebSocket connections."""

    def __init__(self):
        super().__init__('websocket_bridge')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('websocket_host', '0.0.0.0')
        self.declare_parameter('websocket_port', 9090)
        self.declare_parameter('broadcast_rate', 20.0)

        self.ws_host = self.get_parameter('websocket_host').get_parameter_value().string_value
        self.ws_port = self.get_parameter('websocket_port').get_parameter_value().integer_value
        broadcast_rate = self.get_parameter('broadcast_rate').get_parameter_value().double_value

        self.get_logger().info(
            f'WebSocket bridge starting on {self.ws_host}:{self.ws_port}'
        )

        # ── State ────────────────────────────────────────────────────────
        self._connected_clients: Set = set()
        self._latest_state: Dict = {}
        self._lock = threading.Lock()

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(
            DroneState, '/drone_0/drone/state', self._drone_state_cb, 10)
        self.create_subscription(
            String, '/drone_0/drone/current_mode', self._mode_cb, 10)
        self.create_subscription(
            String, '/drone_0/drone/failsafe_status', self._failsafe_cb, 10)
        self.create_subscription(
            String, '/drone_0/drone/mission_state', self._mission_cb, 10)

        # ── Service clients ──────────────────────────────────────────────
        self.set_mode_client = self.create_client(
            SetFlightMode, '/drone_0/drone/set_flight_mode')
        self.update_pid_client = self.create_client(
            UpdatePID, '/drone_0/drone/update_pid')

        # ── Command publisher ────────────────────────────────────────────
        self.mission_cmd_pub = self.create_publisher(
            String, '/drone_0/drone/mission_command', 10)
        self.goal_pub = self.create_publisher(
            PoseStamped, '/drone_0/drone/goal_pose', 10)

        # ── Broadcast timer ──────────────────────────────────────────────
        self.create_timer(1.0 / broadcast_rate, self._broadcast_state)

        self.get_logger().info('WebSocket bridge node initialized')

    # ── ROS callbacks ────────────────────────────────────────────────────
    def _drone_state_cb(self, msg: DroneState):
        with self._lock:
            self._latest_state['drone_state'] = {
                'drone_id': msg.drone_id,
                'latitude': msg.latitude,
                'longitude': msg.longitude,
                'altitude_msl': msg.altitude_msl,
                'altitude_agl': msg.altitude_agl,
                'roll': msg.roll,
                'pitch': msg.pitch,
                'yaw': msg.yaw,
                'ground_speed': msg.ground_speed,
                'vertical_speed': msg.vertical_speed,
                'heading': msg.heading,
                'battery_voltage': msg.battery_voltage,
                'battery_current': msg.battery_current,
                'battery_remaining_pct': msg.battery_remaining_pct,
                'gps_satellites': msg.gps_satellites,
                'gps_hdop': msg.gps_hdop,
                'gps_fix_type': msg.gps_fix_type,
                'rssi': msg.rssi,
                'armed': msg.armed,
                'in_air': msg.in_air,
                'flight_mode': msg.flight_mode,
                'status': msg.status,
            }

    def _mode_cb(self, msg: String):
        with self._lock:
            self._latest_state['current_mode'] = msg.data

    def _failsafe_cb(self, msg: String):
        with self._lock:
            self._latest_state['failsafe'] = msg.data

    def _mission_cb(self, msg: String):
        with self._lock:
            self._latest_state['mission'] = msg.data

    def _broadcast_state(self):
        """Trigger WebSocket broadcast (actual send happens in asyncio loop)."""
        pass  # Broadcast is driven by the asyncio WebSocket handler

    # ── Command handlers ─────────────────────────────────────────────────
    def handle_command(self, command: Dict):
        """Process a command received from the Electron app."""
        cmd_type = command.get('type', '')
        self.get_logger().info(f'Command received: {cmd_type}')

        if cmd_type == 'set_mode':
            self._handle_set_mode(command)
        elif cmd_type == 'update_pid':
            self._handle_update_pid(command)
        elif cmd_type == 'mission':
            self._handle_mission_command(command)
        elif cmd_type == 'goto':
            self._handle_goto(command)
        else:
            self.get_logger().warn(f'Unknown command type: {cmd_type}')

    def _handle_set_mode(self, cmd: Dict):
        if not self.set_mode_client.wait_for_service(timeout_sec=2.0):
            return
        req = SetFlightMode.Request()
        req.drone_id = cmd.get('drone_id', 'drone_0')
        req.mode = cmd.get('mode', '')
        self.set_mode_client.call_async(req)

    def _handle_update_pid(self, cmd: Dict):
        if not self.update_pid_client.wait_for_service(timeout_sec=2.0):
            return
        req = UpdatePID.Request()
        req.drone_id = cmd.get('drone_id', 'drone_0')
        req.axis = cmd.get('axis', 'roll')
        req.p = float(cmd.get('p', 0.0))
        req.i = float(cmd.get('i', 0.0))
        req.d = float(cmd.get('d', 0.0))
        self.update_pid_client.call_async(req)

    def _handle_mission_command(self, cmd: Dict):
        msg = String()
        msg.data = cmd.get('action', '')
        self.mission_cmd_pub.publish(msg)

    def _handle_goto(self, cmd: Dict):
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(cmd.get('x', 0.0))
        pose.pose.position.y = float(cmd.get('y', 0.0))
        pose.pose.position.z = float(cmd.get('z', 5.0))
        pose.pose.orientation.w = 1.0
        self.goal_pub.publish(pose)

    def get_state_json(self) -> str:
        with self._lock:
            return json.dumps({
                'type': 'state_update',
                'data': self._latest_state,
            })


# ── WebSocket server ─────────────────────────────────────────────────────
async def ws_handler(websocket, node: WebSocketBridgeNode):
    """Handle a single WebSocket connection."""
    node._connected_clients.add(websocket)
    node.get_logger().info(
        f'Client connected. Total: {len(node._connected_clients)}'
    )

    try:
        # Send state updates and receive commands
        async for message in websocket:
            try:
                command = json.loads(message)
                node.handle_command(command)
            except json.JSONDecodeError:
                node.get_logger().warn(f'Invalid JSON: {message[:100]}')
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        node._connected_clients.discard(websocket)
        node.get_logger().info(
            f'Client disconnected. Total: {len(node._connected_clients)}'
        )


async def broadcast_loop(node: WebSocketBridgeNode):
    """Periodically broadcast state to all connected clients."""
    while rclpy.ok():
        if node._connected_clients:
            state_json = node.get_state_json()
            disconnected = set()
            for ws in node._connected_clients.copy():
                try:
                    await ws.send(state_json)
                except websockets.exceptions.ConnectionClosed:
                    disconnected.add(ws)
            node._connected_clients -= disconnected
        await asyncio.sleep(0.05)  # 20 Hz


async def run_websocket_server(node: WebSocketBridgeNode):
    """Start the WebSocket server and broadcast loop."""
    server = await websockets.serve(
        lambda ws: ws_handler(ws, node),
        node.ws_host,
        node.ws_port,
    )
    node.get_logger().info(
        f'WebSocket server listening on ws://{node.ws_host}:{node.ws_port}'
    )

    broadcast_task = asyncio.create_task(broadcast_loop(node))

    await asyncio.gather(
        server.wait_closed(),
        broadcast_task,
    )


def main(args=None):
    rclpy.init(args=args)
    node = WebSocketBridgeNode()

    # Run ROS 2 executor in a separate thread
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    ros_thread = threading.Thread(target=executor.spin, daemon=True)
    ros_thread.start()

    # Run WebSocket server in the main asyncio loop
    try:
        asyncio.run(run_websocket_server(node))
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
