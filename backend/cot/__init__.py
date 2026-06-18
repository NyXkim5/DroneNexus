"""
CoT (Cursor-on-Target) bridge for OVERWATCH/BULWARK.

Converts Track and Threat objects from the BULWARK world model into CoT XML
and emits them via UDP multicast so ATAK/WinTAK users see OVERWATCH tracks
on their common operating picture.

Public API
----------
    from cot import CoTBridge, format_track_cot, format_threat_cot, format_heartbeat_cot

Default multicast endpoint: 239.2.3.1:6969 (TAK standard).
"""

from cot.formatter import format_heartbeat_cot, format_threat_cot, format_track_cot
from cot.bridge import CoTBridge

__all__ = [
    "CoTBridge",
    "format_track_cot",
    "format_threat_cot",
    "format_heartbeat_cot",
]
