"""
PX4 SITL multi-instance launcher.
Starts N PX4 SITL instances on sequential UDP ports for testing.

Usage:
    python -m simulation.sitl_launcher --drones 6
    python -m simulation.sitl_launcher --drones 6 --px4-dir /path/to/PX4-Autopilot
"""
import argparse
import subprocess
import os
import sys
import signal
import time


def main():
    parser = argparse.ArgumentParser(description="Launch PX4 SITL instances")
    parser.add_argument("--drones", type=int, default=6, help="Number of SITL instances")
    parser.add_argument("--base-port", type=int, default=14540, help="Base UDP port")
    parser.add_argument("--px4-dir", type=str, default=None,
                        help="Path to PX4-Autopilot directory")
    args = parser.parse_args()

    if args.px4_dir and not os.path.exists(args.px4_dir):
        print(f"Error: PX4 directory not found: {args.px4_dir}")
        sys.exit(1)

    processes = []

    print(f"Starting {args.drones} PX4 SITL instances...")
    print(f"Base port: {args.base_port}")
    print()

    for i in range(args.drones):
        port = args.base_port + i
        instance = i

        if args.px4_dir:
            # Real PX4 SITL
            cmd = [
                "make", "px4_sitl", "gazebo",
                f"INSTANCE={instance}",
            ]
            env = os.environ.copy()
            env["PX4_SYS_AUTOSTART"] = "4001"
            env["PX4_GZ_MODEL_POSE"] = f"{i * 3},0,0,0,0,0"

            print(f"  [{i}] PX4 SITL on port {port} (instance {instance})")
            proc = subprocess.Popen(
                cmd,
                cwd=args.px4_dir,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Placeholder — print instructions
            print(f"  [{i}] Port {port} — PX4 SITL instance {instance}")
            print(f"        Connect: udp://:{port}")
            proc = None

        if proc:
            processes.append(proc)

    if not args.px4_dir:
        print()
        print("No --px4-dir specified. To launch real SITL instances:")
        print()
        print("  Option 1: PX4 native SITL")
        print("    cd /path/to/PX4-Autopilot")
        print(f"    python -m simulation.sitl_launcher --drones {args.drones} --px4-dir .")
        print()
        print("  Option 2: Docker (see docker-compose.yml)")
        print("    docker-compose up")
        print()
        print("  Option 3: Use the built-in mock simulator")
        print("    python main.py  (simulation_mode=True by default)")
        print()
        return

    print(f"\n{len(processes)} SITL instances running. Press Ctrl+C to stop.")

    def cleanup(sig, frame):
        print("\nStopping SITL instances...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.wait(timeout=5)
        print("All instances stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    while True:
        time.sleep(1)
        for p in processes:
            if p.poll() is not None:
                print(f"Warning: SITL process {p.pid} exited")


if __name__ == "__main__":
    main()
