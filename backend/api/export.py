"""
OVERWATCH Flight Log Export — KML and CSV endpoints for telemetry data.
"""
import csv
import io
import logging
from datetime import datetime
from typing import Optional
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger("overwatch.export")

export_router = APIRouter()


def _get_app():
    from main import overwatch_app
    return overwatch_app


def _extract_position(packet: dict) -> Optional[dict]:
    """Extract lat, lon, alt from a telemetry packet payload."""
    pos = packet.get("position")
    if not pos:
        return None
    lat = pos.get("lat", 0.0)
    lon = pos.get("lon", 0.0)
    alt_msl = pos.get("alt_msl", 0.0)
    alt_agl = pos.get("alt_agl", 0.0)
    if lat == 0.0 and lon == 0.0:
        return None
    return {"lat": lat, "lon": lon, "alt_msl": alt_msl, "alt_agl": alt_agl}


@export_router.get("/kml")
async def export_kml(
    drone_id: str = Query(..., description="Asset identifier, e.g. ALPHA-1"),
    start: str = Query(..., description="Start time ISO-8601, e.g. 2026-01-01T00:00:00Z"),
    end: str = Query(..., description="End time ISO-8601, e.g. 2026-12-31T23:59:59Z"),
):
    """Export flight path as a KML file with LineString and waypoint placemarks."""
    app = _get_app()
    if not app.db:
        raise HTTPException(503, "Database not available")

    packets = await app.db.get_telemetry_range(drone_id, start, end)
    if not packets:
        raise HTTPException(404, f"No telemetry found for {drone_id} in the given range")

    # Extract position data
    positions = []
    for pkt in packets:
        pos = _extract_position(pkt)
        if pos:
            positions.append({
                "lat": pos["lat"],
                "lon": pos["lon"],
                "alt": pos["alt_msl"],
                "timestamp": pkt.get("timestamp", ""),
            })

    if not positions:
        raise HTTPException(404, "No position data in telemetry range")

    # Build KML XML
    kml_ns = "http://www.opengis.net/kml/2.2"
    kml = Element("kml", xmlns=kml_ns)
    doc = SubElement(kml, "Document")

    name_el = SubElement(doc, "name")
    name_el.text = f"OVERWATCH Flight Log \u2014 {drone_id}"

    # Style for the flight path
    style = SubElement(doc, "Style", id="flightPath")
    line_style = SubElement(style, "LineStyle")
    color_el = SubElement(line_style, "color")
    color_el.text = "ff00ff88"
    width_el = SubElement(line_style, "width")
    width_el.text = "3"

    # Style for waypoint placemarks
    wp_style = SubElement(doc, "Style", id="waypoint")
    icon_style = SubElement(wp_style, "IconStyle")
    icon_scale = SubElement(icon_style, "scale")
    icon_scale.text = "0.8"

    # Flight path LineString
    pm = SubElement(doc, "Placemark")
    pm_name = SubElement(pm, "name")
    pm_name.text = "Flight Path"
    style_url = SubElement(pm, "styleUrl")
    style_url.text = "#flightPath"

    ls = SubElement(pm, "LineString")
    alt_mode = SubElement(ls, "altitudeMode")
    alt_mode.text = "absolute"
    coords_el = SubElement(ls, "coordinates")
    coords_el.text = " ".join(
        f"{p['lon']},{p['lat']},{p['alt']}" for p in positions
    )

    # Waypoint placemarks at start, end, and sampled intervals
    waypoint_indices = _sample_waypoint_indices(len(positions), max_waypoints=20)
    for idx in waypoint_indices:
        p = positions[idx]
        wp_pm = SubElement(doc, "Placemark")
        wp_name = SubElement(wp_pm, "name")
        if idx == 0:
            wp_name.text = "Start"
        elif idx == len(positions) - 1:
            wp_name.text = "End"
        else:
            wp_name.text = f"WP-{idx}"
        wp_style_url = SubElement(wp_pm, "styleUrl")
        wp_style_url.text = "#waypoint"
        wp_desc = SubElement(wp_pm, "description")
        wp_desc.text = (
            f"Time: {p['timestamp']}\n"
            f"Lat: {p['lat']:.6f}, Lon: {p['lon']:.6f}\n"
            f"Alt: {p['alt']:.1f}m MSL"
        )
        wp_point = SubElement(wp_pm, "Point")
        wp_alt_mode = SubElement(wp_point, "altitudeMode")
        wp_alt_mode.text = "absolute"
        wp_coords = SubElement(wp_point, "coordinates")
        wp_coords.text = f"{p['lon']},{p['lat']},{p['alt']}"

    # Serialize
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    kml_bytes = xml_declaration.encode("utf-8") + tostring(kml, encoding="unicode").encode("utf-8")

    filename = f"overwatch_{drone_id}_{start[:10]}_{end[:10]}.kml"
    logger.info(f"KML export: {drone_id}, {len(positions)} points, {filename}")

    return StreamingResponse(
        io.BytesIO(kml_bytes),
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@export_router.get("/csv")
async def export_csv(
    drone_id: str = Query(..., description="Asset identifier, e.g. ALPHA-1"),
    start: str = Query(..., description="Start time ISO-8601, e.g. 2026-01-01T00:00:00Z"),
    end: str = Query(..., description="End time ISO-8601, e.g. 2026-12-31T23:59:59Z"),
):
    """Export telemetry as a CSV file."""
    app = _get_app()
    if not app.db:
        raise HTTPException(503, "Database not available")

    packets = await app.db.get_telemetry_range(drone_id, start, end)
    if not packets:
        raise HTTPException(404, f"No telemetry found for {drone_id} in the given range")

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "drone_id", "lat", "lon", "alt_msl", "alt_agl",
        "heading", "speed", "battery_pct",
    ])

    for pkt in packets:
        pos = pkt.get("position", {})
        vel = pkt.get("velocity", {})
        bat = pkt.get("battery", {})
        writer.writerow([
            pkt.get("timestamp", ""),
            pkt.get("drone_id", drone_id),
            pos.get("lat", ""),
            pos.get("lon", ""),
            pos.get("alt_msl", ""),
            pos.get("alt_agl", ""),
            vel.get("heading", ""),
            vel.get("ground_speed", ""),
            bat.get("remaining_pct", ""),
        ])

    csv_bytes = output.getvalue().encode("utf-8")
    filename = f"overwatch_{drone_id}_{start[:10]}_{end[:10]}.csv"
    logger.info(f"CSV export: {drone_id}, {len(packets)} rows, {filename}")

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _sample_waypoint_indices(total: int, max_waypoints: int = 20) -> list:
    """Return indices for start, end, and evenly sampled intermediate points."""
    if total <= 0:
        return []
    if total <= max_waypoints:
        return list(range(total))

    indices = [0]
    step = (total - 1) / (max_waypoints - 1)
    for i in range(1, max_waypoints - 1):
        indices.append(int(i * step))
    indices.append(total - 1)
    return indices
