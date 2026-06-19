#!/usr/bin/env python3
"""
Unified drone detection dataset preparation tool.

Ingests datasets in YOLO, COCO, and Pascal VOC formats, harmonizes class
labels to a standard aerial-object taxonomy, applies quality filters, and
outputs a clean YOLO-format training dataset with stratified splits.

Run from cwd=backend:
    python -m scripts.prepare_drone_dataset \
        --sources seraphim:/data/seraphim,anti_uav:/data/anti_uav \
        --output /data/unified_drone_dataset \
        --format yolo \
        --seed 42
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("overwatch.scripts.prepare_drone_dataset")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

UNIFIED_CLASSES: Dict[int, str] = {
    0: "drone",
    1: "bird",
    2: "airplane",
    3: "helicopter",
    4: "unknown_air",
}

UNIFIED_NAME_TO_ID: Dict[str, int] = {v: k for k, v in UNIFIED_CLASSES.items()}

# Mapping from source dataset labels to unified class names.
# Keys are lowercased for case-insensitive matching.
DEFAULT_CLASS_MAP: Dict[str, str] = {
    # drone variants
    "uav": "drone",
    "drone": "drone",
    "quadcopter": "drone",
    "quadrotor": "drone",
    "multirotor": "drone",
    "dji": "drone",
    "fixed_wing": "drone",
    "fixed-wing": "drone",
    "fpv": "drone",
    "uas": "drone",
    "suas": "drone",
    "micro_uav": "drone",
    "mini_drone": "drone",
    "racing_drone": "drone",
    # bird variants
    "bird": "bird",
    "birds": "bird",
    "flying_bird": "bird",
    # airplane
    "airplane": "airplane",
    "aeroplane": "airplane",
    "aircraft": "airplane",
    "plane": "airplane",
    "jet": "airplane",
    # helicopter
    "helicopter": "helicopter",
    "heli": "helicopter",
    "rotorcraft": "helicopter",
    # catch-all aerial
    "unknown_air": "unknown_air",
    "flying_object": "unknown_air",
    "ufo": "unknown_air",
}

# Non-aerial classes to filter out
NON_AERIAL: set[str] = {
    "person", "car", "truck", "bicycle", "motorcycle", "bus", "train",
    "dog", "cat", "horse", "cow", "sheep", "boat", "bench", "chair",
    "bottle", "cell phone", "laptop", "tv", "traffic light", "fire hydrant",
    "stop sign", "parking meter", "backpack", "umbrella", "handbag",
    "suitcase", "sports ball", "kite", "skateboard", "surfboard",
    "tennis racket", "potted plant", "bed", "dining table", "toilet",
    "mouse", "keyboard", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
}

MIN_BBOX_AREA = 0.001
MAX_BBOX_AREA = 0.5


@dataclass
class BBox:
    """Bounding box in normalized YOLO format (center-x, center-y, w, h)."""
    class_id: int
    class_name: str
    cx: float
    cy: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass
class AnnotatedImage:
    """Single image with its list of bounding-box annotations."""
    image_path: Path
    annotations: List[BBox]
    source_dataset: str


@dataclass
class DatasetStats:
    """Aggregated statistics for a prepared dataset."""
    total_images: int = 0
    per_class_counts: Dict[str, int] = field(default_factory=lambda: Counter())
    filtered_no_aerial: int = 0
    filtered_small_bbox: int = 0
    filtered_large_bbox: int = 0
    filtered_duplicate: int = 0
    filtered_unmapped: int = 0
    split_sizes: Dict[str, int] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=lambda: Counter())


# ---------------------------------------------------------------------------
# Class harmonization
# ---------------------------------------------------------------------------


def harmonize_class(
    raw_name: str,
    class_map: Dict[str, str],
) -> Optional[str]:
    """Map a raw class name to a unified class, or None if non-aerial."""
    key = raw_name.strip().lower().replace(" ", "_")
    if key in NON_AERIAL:
        return None
    mapped = class_map.get(key)
    if mapped and mapped in UNIFIED_NAME_TO_ID:
        return mapped
    return None


def build_bbox(
    raw_name: str,
    cx: float,
    cy: float,
    w: float,
    h: float,
    class_map: Dict[str, str],
) -> Optional[BBox]:
    """Build a BBox if the class is aerial and coords are valid."""
    unified = harmonize_class(raw_name, class_map)
    if unified is None:
        return None
    cx, cy, w, h = float(cx), float(cy), float(w), float(h)
    if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0):
        return None
    w = min(max(w, 0.0), 1.0)
    h = min(max(h, 0.0), 1.0)
    return BBox(
        class_id=UNIFIED_NAME_TO_ID[unified],
        class_name=unified,
        cx=cx, cy=cy, w=w, h=h,
    )


# ---------------------------------------------------------------------------
# Multi-format parsers
# ---------------------------------------------------------------------------


def _discover_images(directory: Path) -> Dict[str, Path]:
    """Return stem -> path for all image files in a directory."""
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
    result: Dict[str, Path] = {}
    if not directory.is_dir():
        return result
    for f in sorted(directory.iterdir()):
        if f.suffix.lower() in exts and f.is_file():
            result[f.stem] = f
    return result


def parse_yolo_dataset(
    dir_path: str | Path,
    source_name: str = "yolo",
    class_names: Optional[Dict[int, str]] = None,
    class_map: Optional[Dict[str, str]] = None,
) -> List[AnnotatedImage]:
    """Parse a YOLO-format dataset (images/ + labels/ with .txt files)."""
    root = Path(dir_path)
    cmap = class_map or DEFAULT_CLASS_MAP
    images: List[AnnotatedImage] = []

    img_dirs = _find_yolo_image_dirs(root)
    label_dirs = _find_yolo_label_dirs(root)

    cls_names = class_names or _load_yolo_class_names(root)

    for img_dir, label_dir in zip(img_dirs, label_dirs):
        parsed = _parse_yolo_split(img_dir, label_dir, cls_names, cmap, source_name)
        images.extend(parsed)

    if not img_dirs:
        img_dir = root / "images"
        label_dir = root / "labels"
        if img_dir.is_dir() and label_dir.is_dir():
            images.extend(
                _parse_yolo_split(img_dir, label_dir, cls_names, cmap, source_name),
            )

    return images


def _find_yolo_image_dirs(root: Path) -> List[Path]:
    """Find image subdirectories in a YOLO dataset."""
    images_root = root / "images"
    if not images_root.is_dir():
        return []
    subdirs = sorted(d for d in images_root.iterdir() if d.is_dir())
    return subdirs if subdirs else []


def _find_yolo_label_dirs(root: Path) -> List[Path]:
    """Find label subdirectories matching image subdirectories."""
    labels_root = root / "labels"
    if not labels_root.is_dir():
        return []
    subdirs = sorted(d for d in labels_root.iterdir() if d.is_dir())
    return subdirs if subdirs else []


def _load_yolo_class_names(root: Path) -> Dict[int, str]:
    """Try to read class names from data.yaml or dataset.yaml."""
    for name in ("data.yaml", "dataset.yaml", "classes.yaml"):
        yaml_path = root / name
        if yaml_path.exists():
            return _parse_yaml_names(yaml_path)
    return {}


def _parse_yaml_names(yaml_path: Path) -> Dict[int, str]:
    """Extract the names mapping from a YOLO dataset YAML file."""
    names: Dict[int, str] = {}
    try:
        import yaml  # type: ignore[import-untyped]
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        raw = data.get("names", {})
        if isinstance(raw, dict):
            names = {int(k): str(v) for k, v in raw.items()}
        elif isinstance(raw, list):
            names = {i: str(v) for i, v in enumerate(raw)}
    except Exception:
        logger.warning("Could not parse YAML class names from %s", yaml_path)
    return names


def _parse_yolo_split(
    img_dir: Path,
    label_dir: Path,
    cls_names: Dict[int, str],
    class_map: Dict[str, str],
    source_name: str,
) -> List[AnnotatedImage]:
    """Parse one split (train/val/test) of YOLO labels."""
    images_map = _discover_images(img_dir)
    results: List[AnnotatedImage] = []

    for stem, img_path in images_map.items():
        label_file = label_dir / f"{stem}.txt"
        bboxes = _read_yolo_label(label_file, cls_names, class_map)
        results.append(AnnotatedImage(
            image_path=img_path,
            annotations=bboxes,
            source_dataset=source_name,
        ))
    return results


def _read_yolo_label(
    label_path: Path,
    cls_names: Dict[int, str],
    class_map: Dict[str, str],
) -> List[BBox]:
    """Read a single YOLO .txt label file into BBox list."""
    bboxes: List[BBox] = []
    if not label_path.exists():
        return bboxes
    for line in label_path.read_text().strip().splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        cid = int(parts[0])
        raw_name = cls_names.get(cid, str(cid))
        bbox = build_bbox(raw_name, float(parts[1]), float(parts[2]),
                          float(parts[3]), float(parts[4]), class_map)
        if bbox is not None:
            bboxes.append(bbox)
    return bboxes


def parse_coco_dataset(
    json_path: str | Path,
    images_dir: str | Path,
    source_name: str = "coco",
    class_map: Optional[Dict[str, str]] = None,
) -> List[AnnotatedImage]:
    """Parse a COCO-format dataset (annotations JSON + images directory)."""
    json_path = Path(json_path)
    images_dir = Path(images_dir)
    cmap = class_map or DEFAULT_CLASS_MAP

    with open(json_path) as f:
        data = json.load(f)

    cat_id_to_name = _build_coco_category_map(data.get("categories", []))
    img_id_to_info = _build_coco_image_map(data.get("images", []))
    grouped = _group_coco_annotations(data.get("annotations", []))

    return _assemble_coco_images(
        img_id_to_info, grouped, cat_id_to_name, images_dir, cmap, source_name,
    )


def _build_coco_category_map(categories: List[Dict]) -> Dict[int, str]:
    """Map COCO category IDs to names."""
    return {c["id"]: c["name"] for c in categories}


def _build_coco_image_map(images: List[Dict]) -> Dict[int, Dict]:
    """Map COCO image IDs to image info dicts."""
    return {img["id"]: img for img in images}


def _group_coco_annotations(
    annotations: List[Dict],
) -> Dict[int, List[Dict]]:
    """Group COCO annotations by image_id."""
    grouped: Dict[int, List[Dict]] = defaultdict(list)
    for ann in annotations:
        grouped[ann["image_id"]].append(ann)
    return grouped


def _assemble_coco_images(
    img_map: Dict[int, Dict],
    grouped: Dict[int, List[Dict]],
    cat_map: Dict[int, str],
    images_dir: Path,
    class_map: Dict[str, str],
    source_name: str,
) -> List[AnnotatedImage]:
    """Convert grouped COCO annotations to AnnotatedImage list."""
    results: List[AnnotatedImage] = []
    for img_id, info in img_map.items():
        img_path = images_dir / info["file_name"]
        img_w, img_h = info["width"], info["height"]
        anns = grouped.get(img_id, [])
        bboxes = _convert_coco_bboxes(anns, cat_map, img_w, img_h, class_map)
        results.append(AnnotatedImage(
            image_path=img_path,
            annotations=bboxes,
            source_dataset=source_name,
        ))
    return results


def _convert_coco_bboxes(
    anns: List[Dict],
    cat_map: Dict[int, str],
    img_w: int,
    img_h: int,
    class_map: Dict[str, str],
) -> List[BBox]:
    """Convert COCO bbox [x, y, w, h] absolute to normalized center."""
    bboxes: List[BBox] = []
    for ann in anns:
        cat_name = cat_map.get(ann["category_id"], "")
        x, y, bw, bh = ann["bbox"]
        cx = (x + bw / 2) / img_w
        cy = (y + bh / 2) / img_h
        nw = bw / img_w
        nh = bh / img_h
        bbox = build_bbox(cat_name, cx, cy, nw, nh, class_map)
        if bbox is not None:
            bboxes.append(bbox)
    return bboxes


def parse_voc_dataset(
    dir_path: str | Path,
    source_name: str = "voc",
    class_map: Optional[Dict[str, str]] = None,
) -> List[AnnotatedImage]:
    """Parse a Pascal VOC dataset (Annotations/*.xml + JPEGImages/)."""
    root = Path(dir_path)
    cmap = class_map or DEFAULT_CLASS_MAP
    ann_dir = _find_voc_annotations_dir(root)
    img_dir = _find_voc_images_dir(root)

    if ann_dir is None:
        logger.warning("No VOC Annotations directory found in %s", root)
        return []

    results: List[AnnotatedImage] = []
    for xml_file in sorted(ann_dir.glob("*.xml")):
        item = _parse_voc_xml(xml_file, img_dir, cmap, source_name)
        if item is not None:
            results.append(item)
    return results


def _find_voc_annotations_dir(root: Path) -> Optional[Path]:
    """Locate the VOC annotations directory."""
    for name in ("Annotations", "annotations", "labels"):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def _find_voc_images_dir(root: Path) -> Path:
    """Locate the VOC images directory."""
    for name in ("JPEGImages", "images", "imgs"):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return root


def _parse_voc_xml(
    xml_path: Path,
    img_dir: Path,
    class_map: Dict[str, str],
    source_name: str,
) -> Optional[AnnotatedImage]:
    """Parse a single Pascal VOC XML annotation file."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        logger.warning("Malformed XML: %s", xml_path)
        return None
    root_el = tree.getroot()
    filename = _voc_filename(root_el)
    img_w, img_h = _voc_image_size(root_el)
    if img_w <= 0 or img_h <= 0:
        return None

    img_path = img_dir / filename
    bboxes = _voc_objects_to_bboxes(root_el, img_w, img_h, class_map)
    return AnnotatedImage(
        image_path=img_path,
        annotations=bboxes,
        source_dataset=source_name,
    )


def _voc_filename(root_el: ET.Element) -> str:
    """Extract filename from VOC XML root."""
    el = root_el.find("filename")
    return el.text.strip() if el is not None and el.text else ""


def _voc_image_size(root_el: ET.Element) -> Tuple[int, int]:
    """Extract (width, height) from VOC XML size element."""
    size_el = root_el.find("size")
    if size_el is None:
        return 0, 0
    w_el = size_el.find("width")
    h_el = size_el.find("height")
    w = int(w_el.text) if w_el is not None and w_el.text else 0
    h = int(h_el.text) if h_el is not None and h_el.text else 0
    return w, h


def _voc_objects_to_bboxes(
    root_el: ET.Element,
    img_w: int,
    img_h: int,
    class_map: Dict[str, str],
) -> List[BBox]:
    """Convert all VOC <object> elements to normalized BBox list."""
    bboxes: List[BBox] = []
    for obj in root_el.findall("object"):
        name_el = obj.find("name")
        if name_el is None or not name_el.text:
            continue
        bndbox = obj.find("bndbox")
        if bndbox is None:
            continue
        bbox = _voc_bndbox_to_bbox(name_el.text, bndbox, img_w, img_h, class_map)
        if bbox is not None:
            bboxes.append(bbox)
    return bboxes


def _voc_bndbox_to_bbox(
    raw_name: str,
    bndbox: ET.Element,
    img_w: int,
    img_h: int,
    class_map: Dict[str, str],
) -> Optional[BBox]:
    """Convert a single VOC bndbox to a normalized BBox."""
    try:
        xmin = float(bndbox.findtext("xmin", "0"))
        ymin = float(bndbox.findtext("ymin", "0"))
        xmax = float(bndbox.findtext("xmax", "0"))
        ymax = float(bndbox.findtext("ymax", "0"))
    except ValueError:
        return None
    bw = xmax - xmin
    bh = ymax - ymin
    cx = (xmin + bw / 2) / img_w
    cy = (ymin + bh / 2) / img_h
    nw = bw / img_w
    nh = bh / img_h
    return build_bbox(raw_name, cx, cy, nw, nh, class_map)


# ---------------------------------------------------------------------------
# Auto-detection of dataset format
# ---------------------------------------------------------------------------


def detect_format(path: str | Path) -> str:
    """Guess dataset format from directory structure.

    Priority: check for COCO JSON first, then VOC XML, then fall back to YOLO.
    """
    p = Path(path)
    if _has_coco_json(p):
        return "coco"
    if _has_voc_structure(p):
        return "voc"
    return "yolo"


def _has_coco_json(p: Path) -> bool:
    """Check if directory contains a COCO-format JSON file."""
    for f in p.glob("*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            if "annotations" in data and "images" in data:
                return True
        except (json.JSONDecodeError, KeyError):
            continue
    return False


def _has_voc_structure(p: Path) -> bool:
    """Check if directory has Pascal VOC structure (Annotations + images)."""
    ann_dirs = ("Annotations", "annotations")
    img_dirs = ("JPEGImages", "images", "imgs")
    has_ann = any((p / d).is_dir() for d in ann_dirs)
    has_img = any((p / d).is_dir() for d in img_dirs)
    return has_ann and has_img


def _find_coco_json(path: Path) -> Optional[Path]:
    """Find the COCO annotations JSON in a directory."""
    for f in sorted(path.glob("*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            if "annotations" in data and "images" in data:
                return f
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def _find_coco_images_dir(path: Path) -> Path:
    """Find the images directory for a COCO dataset."""
    for name in ("images", "train2017", "val2017", "JPEGImages"):
        candidate = path / name
        if candidate.is_dir():
            return candidate
    return path


def parse_auto(
    path: str | Path,
    source_name: str,
    class_map: Optional[Dict[str, str]] = None,
) -> List[AnnotatedImage]:
    """Auto-detect format and parse a dataset."""
    p = Path(path)
    fmt = detect_format(p)
    logger.info("Detected format '%s' for source '%s' at %s", fmt, source_name, p)

    if fmt == "coco":
        json_path = _find_coco_json(p)
        if json_path is None:
            logger.error("No COCO JSON found in %s", p)
            return []
        img_dir = _find_coco_images_dir(p)
        return parse_coco_dataset(json_path, img_dir, source_name, class_map)
    if fmt == "voc":
        return parse_voc_dataset(p, source_name, class_map)
    return parse_yolo_dataset(p, source_name, class_map=class_map)


# ---------------------------------------------------------------------------
# Quality filters
# ---------------------------------------------------------------------------


def compute_md5(filepath: Path) -> str:
    """Compute MD5 hex digest for an image file."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def filter_annotations(bboxes: List[BBox]) -> Tuple[List[BBox], int, int]:
    """Remove bboxes that are too small or too large. Returns (kept, small, large)."""
    kept: List[BBox] = []
    small_count = 0
    large_count = 0
    for bb in bboxes:
        area = bb.area
        if area < MIN_BBOX_AREA:
            small_count += 1
        elif area > MAX_BBOX_AREA:
            large_count += 1
        else:
            kept.append(bb)
    return kept, small_count, large_count


def apply_quality_filters(
    images: List[AnnotatedImage],
    deduplicate: bool = True,
) -> Tuple[List[AnnotatedImage], DatasetStats]:
    """Apply all quality filters and return clean images + stats."""
    stats = DatasetStats()
    seen_hashes: set[str] = set()
    clean: List[AnnotatedImage] = []

    for item in images:
        kept, n_small, n_large = filter_annotations(item.annotations)
        stats.filtered_small_bbox += n_small
        stats.filtered_large_bbox += n_large

        if not kept:
            stats.filtered_no_aerial += 1
            continue

        if deduplicate and item.image_path.exists():
            md5 = compute_md5(item.image_path)
            if md5 in seen_hashes:
                stats.filtered_duplicate += 1
                continue
            seen_hashes.add(md5)

        item_clean = AnnotatedImage(
            image_path=item.image_path,
            annotations=kept,
            source_dataset=item.source_dataset,
        )
        clean.append(item_clean)
        stats.source_counts[item.source_dataset] += 1
        for bb in kept:
            stats.per_class_counts[bb.class_name] += 1

    stats.total_images = len(clean)
    return clean, stats


# ---------------------------------------------------------------------------
# Stratified split
# ---------------------------------------------------------------------------


def stratified_split(
    images: List[AnnotatedImage],
    train_ratio: float = 0.7,
    val_ratio: float = 0.2,
    seed: int = 42,
) -> Tuple[List[AnnotatedImage], List[AnnotatedImage], List[AnnotatedImage]]:
    """Split images into train/val/test with stratified class distribution."""
    rng = random.Random(seed)
    class_buckets = _bucket_by_primary_class(images)
    train, val, test = _split_buckets(class_buckets, train_ratio, val_ratio, rng)
    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)
    return train, val, test


def _bucket_by_primary_class(
    images: List[AnnotatedImage],
) -> Dict[int, List[AnnotatedImage]]:
    """Group images by their most frequent class_id."""
    buckets: Dict[int, List[AnnotatedImage]] = defaultdict(list)
    for img in images:
        if not img.annotations:
            buckets[-1].append(img)
            continue
        counts: Counter = Counter(bb.class_id for bb in img.annotations)
        primary = counts.most_common(1)[0][0]
        buckets[primary].append(img)
    return buckets


def _split_buckets(
    buckets: Dict[int, List[AnnotatedImage]],
    train_ratio: float,
    val_ratio: float,
    rng: random.Random,
) -> Tuple[List[AnnotatedImage], List[AnnotatedImage], List[AnnotatedImage]]:
    """Split each class bucket proportionally, then merge."""
    train: List[AnnotatedImage] = []
    val: List[AnnotatedImage] = []
    test: List[AnnotatedImage] = []
    for _cid, items in sorted(buckets.items()):
        rng.shuffle(items)
        n = len(items)
        n_train = max(1, int(n * train_ratio)) if n > 0 else 0
        n_val = max(1, int(n * val_ratio)) if n > 1 else 0
        if n_train + n_val > n:
            n_val = n - n_train
        train.extend(items[:n_train])
        val.extend(items[n_train:n_train + n_val])
        test.extend(items[n_train + n_val:])
    return train, val, test


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------


def write_yolo_dataset(
    train: List[AnnotatedImage],
    val: List[AnnotatedImage],
    test: List[AnnotatedImage],
    output_dir: Path,
) -> Path:
    """Write the unified YOLO dataset to disk. Returns path to dataset.yaml."""
    _write_split(train, output_dir, "train")
    _write_split(val, output_dir, "val")
    _write_split(test, output_dir, "test")
    yaml_path = _write_dataset_yaml(output_dir)
    return yaml_path


def _write_split(
    items: List[AnnotatedImage],
    output_dir: Path,
    split_name: str,
) -> None:
    """Write one split's images and labels to the output directory."""
    img_out = output_dir / "images" / split_name
    lbl_out = output_dir / "labels" / split_name
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    for i, item in enumerate(items):
        ext = item.image_path.suffix or ".jpg"
        stem = f"{item.source_dataset}_{i:06d}"
        dst_img = img_out / f"{stem}{ext}"
        dst_lbl = lbl_out / f"{stem}.txt"

        if item.image_path.exists():
            shutil.copy2(item.image_path, dst_img)
        else:
            _write_placeholder_reference(dst_img, item.image_path)

        _write_yolo_label(dst_lbl, item.annotations)


def _write_placeholder_reference(dst: Path, original: Path) -> None:
    """Write a text file noting the missing source image."""
    dst.write_text(f"# Source image not found: {original}\n")


def _write_yolo_label(label_path: Path, bboxes: List[BBox]) -> None:
    """Write YOLO-format label file."""
    lines = [
        f"{bb.class_id} {bb.cx:.6f} {bb.cy:.6f} {bb.w:.6f} {bb.h:.6f}"
        for bb in bboxes
    ]
    label_path.write_text("\n".join(lines) + "\n" if lines else "")


def _write_dataset_yaml(output_dir: Path) -> Path:
    """Generate Ultralytics-compatible dataset.yaml."""
    yaml_path = output_dir / "dataset.yaml"
    lines = [
        f"path: {output_dir.resolve()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        f"nc: {len(UNIFIED_CLASSES)}",
        "",
        "names:",
    ]
    for cid, name in sorted(UNIFIED_CLASSES.items()):
        lines.append(f"  {cid}: {name}")
    lines.append("")
    yaml_path.write_text("\n".join(lines))
    logger.info("Wrote dataset.yaml to %s", yaml_path)
    return yaml_path


def write_stats(
    stats: DatasetStats,
    output_dir: Path,
) -> Path:
    """Write stats.json summarizing the prepared dataset."""
    stats_path = output_dir / "stats.json"
    payload = {
        "total_images": stats.total_images,
        "per_class_counts": dict(stats.per_class_counts),
        "split_sizes": stats.split_sizes,
        "source_counts": dict(stats.source_counts),
        "filtered": {
            "no_aerial_annotations": stats.filtered_no_aerial,
            "small_bbox": stats.filtered_small_bbox,
            "large_bbox": stats.filtered_large_bbox,
            "duplicate": stats.filtered_duplicate,
            "unmapped_class": stats.filtered_unmapped,
        },
    }
    stats_path.write_text(json.dumps(payload, indent=2) + "\n")
    logger.info("Wrote stats.json to %s", stats_path)
    return stats_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_sources(sources_str: str) -> List[Tuple[str, Path]]:
    """Parse the --sources flag: 'name:/path,name2:/path2'."""
    result: List[Tuple[str, Path]] = []
    for entry in sources_str.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            name, path_str = entry.split(":", 1)
            result.append((name.strip(), Path(path_str.strip())))
        else:
            p = Path(entry)
            result.append((p.stem, p))
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all flags."""
    parser = argparse.ArgumentParser(
        description="Prepare a unified drone detection dataset from multiple sources",
    )
    parser.add_argument(
        "--sources", type=str, required=True,
        help="Comma-separated name:path pairs, e.g. seraphim:/data/s,anti_uav:/data/a",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output directory for the unified YOLO dataset",
    )
    parser.add_argument(
        "--format", type=str, default="auto",
        choices=["auto", "yolo", "coco", "voc"],
        help="Force input format (default: auto-detect per source)",
    )
    parser.add_argument(
        "--train-ratio", type=float, default=0.7,
        help="Training split ratio (default: 0.7)",
    )
    parser.add_argument(
        "--val-ratio", type=float, default=0.2,
        help="Validation split ratio (default: 0.2)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducible splits (default: 42)",
    )
    parser.add_argument(
        "--no-dedup", action="store_true",
        help="Skip duplicate image detection (faster but may include copies)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    return _build_arg_parser().parse_args(argv)


def print_report(stats: DatasetStats) -> None:
    """Print a summary report to stdout."""
    print("\n" + "=" * 60)
    print("  OVERWATCH Dataset Preparation Report")
    print("=" * 60)
    print(f"  Total images retained : {stats.total_images}")
    print()
    print("  Per-class annotation counts:")
    for name in sorted(stats.per_class_counts):
        print(f"    {name:20s}: {stats.per_class_counts[name]}")
    print()
    print("  Split sizes:")
    for split_name in ("train", "val", "test"):
        print(f"    {split_name:20s}: {stats.split_sizes.get(split_name, 0)}")
    print()
    print("  Source datasets:")
    for src in sorted(stats.source_counts):
        print(f"    {src:20s}: {stats.source_counts[src]}")
    print()
    print("  Filtered out:")
    print(f"    No aerial annots   : {stats.filtered_no_aerial}")
    print(f"    Small bbox (<0.001): {stats.filtered_small_bbox}")
    print(f"    Large bbox (>0.5)  : {stats.filtered_large_bbox}")
    print(f"    Duplicate images   : {stats.filtered_duplicate}")
    print("=" * 60 + "\n")


def _ingest_source(
    name: str,
    path: Path,
    fmt: str,
    class_map: Optional[Dict[str, str]] = None,
) -> List[AnnotatedImage]:
    """Ingest a single source dataset using the specified format."""
    if fmt == "auto":
        return parse_auto(path, name, class_map)
    if fmt == "yolo":
        return parse_yolo_dataset(path, name, class_map=class_map)
    if fmt == "coco":
        coco_json = _find_coco_json(path)
        img_dir = _find_coco_images_dir(path)
        if coco_json is None:
            logger.error("No COCO JSON found in %s", path)
            return []
        return parse_coco_dataset(coco_json, img_dir, name, class_map)
    return parse_voc_dataset(path, name, class_map)


def _ingest_all_sources(
    sources: List[Tuple[str, Path]],
    fmt: str,
) -> List[AnnotatedImage]:
    """Ingest all sources and return combined image list."""
    all_images: List[AnnotatedImage] = []
    for name, path in sources:
        logger.info("Ingesting source '%s' from %s", name, path)
        parsed = _ingest_source(name, path, fmt)
        logger.info("  Parsed %d images from '%s'", len(parsed), name)
        all_images.extend(parsed)
    logger.info("Total raw images: %d", len(all_images))
    return all_images


def main(argv: list[str] | None = None) -> None:
    """Entry point for dataset preparation CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    args = parse_args(argv)
    sources = parse_sources(args.sources)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_images = _ingest_all_sources(sources, args.format)
    clean, stats = apply_quality_filters(all_images, deduplicate=not args.no_dedup)
    logger.info("After filtering: %d images", len(clean))

    train, val, test = stratified_split(
        clean, args.train_ratio, args.val_ratio, args.seed,
    )
    stats.split_sizes = {"train": len(train), "val": len(val), "test": len(test)}

    write_yolo_dataset(train, val, test, output_dir)
    write_stats(stats, output_dir)
    print_report(stats)


if __name__ == "__main__":
    main()
