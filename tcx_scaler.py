"""Scale a Garmin-exported TCX to a target N.NN km total distance.

Strategy: shrink/expand the GPS path uniformly toward the first position so that
the path's total distance equals the target. Speed and cumulative distance fields
are scaled by the same factor; time / HR / cadence / power / altitude are left
untouched (they are measured values, not derived).

The TCX namespace machinery is verbose — we strip-and-rewrite the default
namespace once on load to keep the rest of the code readable.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
TPX_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"

ET.register_namespace("", TCX_NS)


@dataclass
class ScaleResult:
    original_distance_m: float
    target_distance_m: float
    scale_factor: float
    trackpoints_scaled: int
    laps_scaled: int


def _t(tag: str) -> str:
    return f"{{{TCX_NS}}}{tag}"


def _tpx(tag: str) -> str:
    return f"{{{TPX_NS}}}{tag}"


def repeating_target_km(distance_m: float) -> float | None:
    """Round a distance to the N.NN km form. Return None if activity is < 1 km."""
    km = distance_m / 1000.0
    n = int(km)
    if n == 0:
        return None
    return float(f"{n}.{n:02d}")


def compute_actual_distance(root: ET.Element) -> float:
    """Sum of all lap DistanceMeters — authoritative per Garmin's own export."""
    total = 0.0
    for lap in root.iter(_t("Lap")):
        d = lap.find(_t("DistanceMeters"))
        if d is not None and d.text:
            total += float(d.text)
    return total


def scale_tcx(in_path: Path, out_path: Path, target_distance_m: float) -> ScaleResult:
    tree = ET.parse(in_path)
    root = tree.getroot()

    original = compute_actual_distance(root)
    if original <= 0:
        raise ValueError(f"TCX has no positive distance ({original})")

    k = target_distance_m / original

    # Anchor: first trackpoint with a position. Everything scales toward it.
    anchor_lat: float | None = None
    anchor_lon: float | None = None
    for tp in root.iter(_t("Trackpoint")):
        pos = tp.find(_t("Position"))
        if pos is not None:
            lat_el = pos.find(_t("LatitudeDegrees"))
            lon_el = pos.find(_t("LongitudeDegrees"))
            if lat_el is not None and lon_el is not None:
                anchor_lat = float(lat_el.text)
                anchor_lon = float(lon_el.text)
                break
    if anchor_lat is None:
        raise ValueError("No trackpoint with position found")

    tp_count = 0
    for tp in root.iter(_t("Trackpoint")):
        pos = tp.find(_t("Position"))
        if pos is not None:
            lat_el = pos.find(_t("LatitudeDegrees"))
            lon_el = pos.find(_t("LongitudeDegrees"))
            if lat_el is not None and lat_el.text:
                lat = float(lat_el.text)
                lat_el.text = f"{anchor_lat + (lat - anchor_lat) * k:.14f}"
            if lon_el is not None and lon_el.text:
                lon = float(lon_el.text)
                lon_el.text = f"{anchor_lon + (lon - anchor_lon) * k:.14f}"

        d_el = tp.find(_t("DistanceMeters"))
        if d_el is not None and d_el.text:
            d_el.text = f"{float(d_el.text) * k:.6f}"

        # Speed inside the TPX extension — keep pace consistent with scaled path.
        for speed in tp.iter(_tpx("Speed")):
            if speed.text:
                speed.text = f"{float(speed.text) * k:.6f}"
        tp_count += 1

    lap_count = 0
    for lap in root.iter(_t("Lap")):
        d_el = lap.find(_t("DistanceMeters"))
        if d_el is not None and d_el.text:
            d_el.text = f"{float(d_el.text) * k:.6f}"
        # Scale lap-level avg/max speed in LX extension to stay consistent
        for ext_speed_tag in ("AvgSpeed", "MaxSpeed"):
            for s in lap.iter(_tpx(ext_speed_tag)):
                if s.text:
                    s.text = f"{float(s.text) * k:.6f}"
        lap_count += 1

    tree.write(out_path, xml_declaration=True, encoding="UTF-8")

    return ScaleResult(
        original_distance_m=original,
        target_distance_m=target_distance_m,
        scale_factor=k,
        trackpoints_scaled=tp_count,
        laps_scaled=lap_count,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python tcx_scaler.py <input.tcx> <output.tcx> [target_km]", file=sys.stderr)
        sys.exit(1)
    in_p = Path(sys.argv[1])
    out_p = Path(sys.argv[2])
    if len(sys.argv) >= 4:
        target_m = float(sys.argv[3]) * 1000
    else:
        # Auto: N.NN form from current total
        tree = ET.parse(in_p)
        actual = compute_actual_distance(tree.getroot())
        target_km = repeating_target_km(actual)
        if target_km is None:
            print(f"Activity is < 1 km ({actual} m), nothing to do.", file=sys.stderr)
            sys.exit(0)
        target_m = target_km * 1000
        print(f"Auto-target: {actual/1000:.4f} km -> {target_km} km")

    r = scale_tcx(in_p, out_p, target_m)
    print(f"Scaled: {r.original_distance_m/1000:.4f} km -> "
          f"{r.target_distance_m/1000:.4f} km  (k={r.scale_factor:.6f})")
    print(f"  trackpoints: {r.trackpoints_scaled}")
    print(f"  laps: {r.laps_scaled}")
