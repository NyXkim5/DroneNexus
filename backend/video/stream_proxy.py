"""
Video stream proxy — manages video sources per drone.
Converts RTSP/V4L2 sources to MJPEG frames for consumption by the HUD.
Provides both an MJPEG HTTP endpoint and WebSocket frame delivery.
"""
import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Set

logger = logging.getLogger("nexus.video")


@dataclass
class VideoSource:
    name: str
    url: str
    stream_type: str = "rtsp"       # rtsp | mjpeg | v4l2 | test
    codec: str = "h264"
    active: bool = False
    _container: object = field(default=None, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _latest_frame: Optional[bytes] = field(default=None, repr=False)
    _frame_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _subscribers: Set = field(default_factory=set, repr=False)


class VideoStreamManager:
    """Manages video sources per drone and provides frame access."""

    def __init__(self):
        self.sources: Dict[str, VideoSource] = {}
        self._running = False

    def add_source(self, drone_id: str, name: str, url: str,
                   stream_type: str = "rtsp", codec: str = "h264") -> None:
        """Register a video source for a drone."""
        self.sources[drone_id] = VideoSource(
            name=name, url=url, stream_type=stream_type, codec=codec
        )
        logger.info(f"[Video] Added source for {drone_id}: {name} ({stream_type})")

    def remove_source(self, drone_id: str) -> None:
        """Remove and stop a video source."""
        source = self.sources.pop(drone_id, None)
        if source and source._task:
            source._task.cancel()
        logger.info(f"[Video] Removed source for {drone_id}")

    async def start_source(self, drone_id: str) -> bool:
        """Start capturing frames from a video source."""
        source = self.sources.get(drone_id)
        if not source:
            return False
        if source.active:
            return True

        if source.stream_type == "test":
            source._task = asyncio.create_task(self._test_pattern_loop(source))
        elif source.stream_type == "rtsp":
            source._task = asyncio.create_task(self._rtsp_capture_loop(source))
        elif source.stream_type == "mjpeg":
            source._task = asyncio.create_task(self._mjpeg_capture_loop(source))
        elif source.stream_type == "v4l2":
            source._task = asyncio.create_task(self._v4l2_capture_loop(source))
        else:
            logger.warning(f"[Video] Unknown stream type: {source.stream_type}")
            return False

        source.active = True
        return True

    async def stop_source(self, drone_id: str) -> None:
        """Stop capturing from a video source."""
        source = self.sources.get(drone_id)
        if source and source._task:
            source._task.cancel()
            source.active = False

    def get_latest_frame(self, drone_id: str) -> Optional[bytes]:
        """Get the most recent JPEG frame for a drone."""
        source = self.sources.get(drone_id)
        if source:
            return source._latest_frame
        return None

    async def wait_for_frame(self, drone_id: str, timeout: float = 5.0) -> Optional[bytes]:
        """Wait for the next frame from a drone's video source."""
        source = self.sources.get(drone_id)
        if not source:
            return None
        source._frame_event.clear()
        try:
            await asyncio.wait_for(source._frame_event.wait(), timeout)
            return source._latest_frame
        except asyncio.TimeoutError:
            return None

    def list_sources(self) -> List[dict]:
        """List all registered video sources."""
        return [
            {
                'drone_id': did,
                'name': s.name,
                'url': s.url,
                'type': s.stream_type,
                'active': s.active,
            }
            for did, s in self.sources.items()
        ]

    async def _rtsp_capture_loop(self, source: VideoSource) -> None:
        """Decode RTSP stream to JPEG frames using PyAV."""
        try:
            import av
            container = av.open(source.url, options={
                'rtsp_transport': 'tcp',
                'stimeout': '5000000',
                'fflags': 'nobuffer',
                'flags': 'low_delay',
            })
            source._container = container

            for frame in container.decode(video=0):
                img = frame.to_image()
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=80)
                source._latest_frame = buf.getvalue()
                source._frame_event.set()
                source._frame_event.clear()
                await asyncio.sleep(0)

        except ImportError:
            logger.error("[Video] PyAV not installed — run: pip install av")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[Video] RTSP capture error: {e}")
        finally:
            source.active = False
            if source._container:
                source._container.close()

    async def _mjpeg_capture_loop(self, source: VideoSource) -> None:
        """Read MJPEG stream over HTTP."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(source.url) as resp:
                    buffer = b''
                    async for chunk in resp.content.iter_any():
                        buffer += chunk
                        while True:
                            start = buffer.find(b'\xff\xd8')
                            end = buffer.find(b'\xff\xd9', start + 2) if start >= 0 else -1
                            if start >= 0 and end >= 0:
                                frame = buffer[start:end + 2]
                                source._latest_frame = frame
                                source._frame_event.set()
                                source._frame_event.clear()
                                buffer = buffer[end + 2:]
                            else:
                                break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[Video] MJPEG capture error: {e}")
        finally:
            source.active = False

    async def _v4l2_capture_loop(self, source: VideoSource) -> None:
        """Capture from V4L2 device (Linux USB camera)."""
        try:
            import av
            container = av.open(source.url, format='v4l2', options={
                'video_size': '1280x720',
                'framerate': '30',
            })
            source._container = container

            for frame in container.decode(video=0):
                img = frame.to_image()
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=80)
                source._latest_frame = buf.getvalue()
                source._frame_event.set()
                source._frame_event.clear()
                await asyncio.sleep(0)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[Video] V4L2 capture error: {e}")
        finally:
            source.active = False

    async def _test_pattern_loop(self, source: VideoSource) -> None:
        """Generate test pattern frames for development without hardware."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.error("[Video] Pillow not installed for test patterns")
            source.active = False
            return

        try:
            frame_num = 0
            while True:
                img = Image.new('RGB', (640, 480), color=(20, 20, 30))
                draw = ImageDraw.Draw(img)
                draw.text((220, 200), f"NEXUS FPV TEST", fill=(0, 255, 136))
                draw.text((250, 240), f"Frame {frame_num}", fill=(100, 150, 200))
                draw.text((200, 280), time.strftime("%H:%M:%S"), fill=(255, 170, 0))
                # Crosshair
                draw.line([(310, 240), (330, 240)], fill=(0, 255, 136), width=1)
                draw.line([(320, 230), (320, 250)], fill=(0, 255, 136), width=1)

                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=80)
                source._latest_frame = buf.getvalue()
                source._frame_event.set()
                source._frame_event.clear()
                frame_num += 1
                await asyncio.sleep(1 / 30)
        except asyncio.CancelledError:
            pass
        finally:
            source.active = False
