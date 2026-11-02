# context_extractor_invalid.py
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import pymysql
import uiautomator2 as u2
import yaml


PROJECT_ROOT = Path(
    "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study"
)

PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "configs" / "prompt_templates.yaml"

OUTPUT_ROOT = PROJECT_ROOT / "tosem_major" / "apk_info"
SCREENSHOT_DIR = OUTPUT_ROOT / "screenshots"
XML_DIR = OUTPUT_ROOT / "xml_dumps"

DB_HOST = os.getenv("TOSEM_DB_HOST", "101.200.47.200")
DB_PORT = int(os.getenv("TOSEM_DB_PORT", "3306"))
DB_USER = os.getenv("TOSEM_DB_USER", "must_user")
DB_PASSWORD = os.getenv("TOSEM_DB_PASSWORD", "REDACTED_USE_ENV_MYSQL_PASSWORD")
DB_NAME = os.getenv("TOSEM_DB_NAME", "tosem_text_input_db")


INPUT_CLASSES = {
    "android.widget.EditText",
    "android.widget.AutoCompleteTextView",
    "android.widget.MultiAutoCompleteTextView",
}

TEXT_CLASSES = {
    "android.widget.TextView",
    "android.widget.Button",
    "android.widget.CheckedTextView",
}

GENERIC_INPUT_RESOURCE_IDS = {
    "de.symeda.sormas.app:id/text_input",
    "de.symeda.sormas.app:id/date_input",
}

CUSTOM_INPUT_RESOURCE_KEYWORDS = {
    "editor",
    "code_editor",
    "text_editor",
}

MAX_COMPONENT_TEXT_LEN = 1000


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_page_id(page_id: int | str) -> int:
    """
    page_id is INT PRIMARY KEY in t_invalid_prompt_context.
    This helper ensures every DB read/write uses an integer page_id.
    """
    try:
        return int(str(page_id).strip())
    except Exception as e:
        raise ValueError(f"Invalid page_id={page_id!r}. It must be convertible to int.") from e


def truncate_text(text: str, max_len: int = MAX_COMPONENT_TEXT_LEN) -> str:
    if text is None:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...[truncated]"


def get_attr(node: ET.Element, key: str, default: str = "") -> str:
    return node.attrib.get(key, default)


def is_custom_text_input_node(node: ET.Element) -> bool:
    cls = get_attr(node, "class", "")
    rid = get_attr(node, "resource-id", "").lower()

    if get_attr(node, "enabled", "false") != "true":
        return False

    visible = get_attr(node, "visible-to-user", "")
    if visible and visible != "true":
        return False

    if get_attr(node, "focusable", "false") != "true":
        return False

    if cls == "android.view.View" and any(k in rid for k in CUSTOM_INPUT_RESOURCE_KEYWORDS):
        return True

    return False


def component_type_name(comp_type: str) -> str:
    if not comp_type:
        return "Input"
    if "." in comp_type:
        return comp_type.split(".")[-1]
    return comp_type


def run_cmd(cmd: List[str], timeout: int = 30) -> str:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
    return result.stdout


def adb_cmd(serial: Optional[str], args: List[str], timeout: int = 30) -> str:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    return run_cmd(cmd, timeout=timeout)


def connect_db() -> pymysql.connections.Connection:
    if not DB_PASSWORD:
        raise RuntimeError(
            "DB password is empty. Please set TOSEM_DB_PASSWORD first, e.g.\n"
            "export TOSEM_DB_PASSWORD='your_password'"
        )

    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def load_templates() -> Dict[str, str]:
    with PROMPT_TEMPLATE_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if "prompt_templates" not in data:
        raise RuntimeError(f"Cannot find 'prompt_templates' in {PROMPT_TEMPLATE_PATH}")

    templates = data["prompt_templates"]
    required = {"GloP", "ComP", "AdjP", "ResP", "GuiP"}
    missing = required - set(templates.keys())
    if missing:
        raise RuntimeError(f"Missing prompt templates in {PROMPT_TEMPLATE_PATH}: {sorted(missing)}")

    return templates


def parse_bounds(bounds: str) -> Dict[str, int]:
    if not bounds:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}

    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not m:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}

    left, top, right, bottom = map(int, m.groups())
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def center(bounds: Dict[str, int]) -> Tuple[float, float]:
    return (
        (bounds["left"] + bounds["right"]) / 2.0,
        (bounds["top"] + bounds["bottom"]) / 2.0,
    )


def is_visible(bounds: Dict[str, int], width: int, height: int) -> bool:
    return (
        0 <= bounds["left"] < width
        and 0 <= bounds["top"] < height
        and bounds["right"] <= width
        and bounds["bottom"] <= height
        and bounds["right"] > bounds["left"]
        and bounds["bottom"] > bounds["top"]
    )


def get_screen_size(serial: Optional[str]) -> Tuple[int, int]:
    out = adb_cmd(serial, ["shell", "wm", "size"], timeout=10)
    m = re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    if not m:
        return 1080, 1920
    return int(m.group(1)), int(m.group(2))


def get_current_activity(device: u2.Device, serial: Optional[str], page_id: int) -> str:
    try:
        info = device.app_current()
        activity = info.get("activity", "")
        if activity:
            return activity.split(".")[-1]
    except Exception as e:
        log(f"[WARN] Failed to read current activity from uiautomator2: {e}")

    try:
        out = adb_cmd(serial, ["shell", "dumpsys", "window", "windows"], timeout=10)
        patterns = [
            r"mCurrentFocus=.*?\s[A-Za-z0-9_.]+/([A-Za-z0-9_.$]+)",
            r"mFocusedApp=.*?\s[A-Za-z0-9_.]+/([A-Za-z0-9_.$]+)",
        ]
        for pat in patterns:
            m = re.search(pat, out)
            if m:
                return m.group(1).split(".")[-1]
    except Exception as e:
        log(f"[WARN] Failed to read current activity from dumpsys: {e}")

    return f"page_{page_id}"


def dump_raw_xml(device: u2.Device) -> Tuple[str, ET.Element]:
    raw_xml = device.dump_hierarchy(compressed=False)
    root = ET.fromstring(raw_xml.encode("utf-8"))
    xml_text = ET.tostring(root, encoding="utf-8").decode("utf-8")
    return xml_text, root


def save_xml(xml_text: str, page_id: int) -> Path:
    page_id = normalize_page_id(page_id)

    XML_DIR.mkdir(parents=True, exist_ok=True)
    path = XML_DIR / f"hierarchy_{page_id}.xml"
    path.write_text(xml_text, encoding="utf-8")
    return path


def save_screenshot(serial: Optional[str], page_id: int) -> Path:
    page_id = normalize_page_id(page_id)

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"screenshot_{page_id}.png"

    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "screencap", "-p"]

    with path.open("wb") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=30)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to save screenshot: {result.stderr.decode(errors='replace')}")

    return path


def image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def build_parent_map(root: ET.Element) -> Dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in list(parent)}


def find_nearest_parent_resource_id(
    node: ET.Element,
    parent_map: Dict[ET.Element, ET.Element],
) -> str:
    ignored_ids = {
        "android:id/content",
        "de.symeda.sormas.app:id/action_bar_root",
        "de.symeda.sormas.app:id/base_layout",
        "de.symeda.sormas.app:id/drawer_layout",
        "de.symeda.sormas.app:id/fragment_frame",
        "de.symeda.sormas.app:id/childFragmentFrame",
        "de.symeda.sormas.app:id/main_content",
        "de.symeda.sormas.app:id/label_frame",
    } | GENERIC_INPUT_RESOURCE_IDS

    current = node
    while current in parent_map:
        current = parent_map[current]
        rid = get_attr(current, "resource-id", "")
        if rid and rid not in ignored_ids:
            return rid

    return ""


def find_input_nodes(root: ET.Element) -> List[ET.Element]:
    nodes: List[ET.Element] = []

    for node in root.iter("node"):
        cls = get_attr(node, "class")

        if cls in INPUT_CLASSES:
            nodes.append(node)
            continue

        if is_custom_text_input_node(node):
            nodes.append(node)
            continue

    seen = set()
    unique = []

    for node in nodes:
        key = (
            get_attr(node, "resource-id"),
            get_attr(node, "bounds"),
            get_attr(node, "class"),
            get_attr(node, "index"),
            get_attr(node, "hint"),
            truncate_text(get_attr(node, "text", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(node)

    return unique


def extract_component_contexts(
    root: ET.Element,
    screen_width: int,
    screen_height: int,
    require_clickable: bool = False,
) -> List[Dict[str, Any]]:
    input_nodes = find_input_nodes(root)
    parent_map = build_parent_map(root)
    components: List[Dict[str, Any]] = []

    for node in input_nodes:
        bounds = parse_bounds(get_attr(node, "bounds", ""))
        clickable = get_attr(node, "clickable", "false")
        cls = get_attr(node, "class", "")

        if cls in INPUT_CLASSES:
            component_type = cls
        elif is_custom_text_input_node(node):
            component_type = "CustomTextEditor"
        else:
            component_type = cls or "Input"

        if not is_visible(bounds, screen_width, screen_height):
            continue

        if require_clickable and clickable != "true":
            if not is_custom_text_input_node(node):
                continue

        raw_resource_id = get_attr(node, "resource-id", "")

        if (not raw_resource_id) or (raw_resource_id in GENERIC_INPUT_RESOURCE_IDS):
            parent_rid = find_nearest_parent_resource_id(node, parent_map)

            if parent_rid:
                if raw_resource_id in GENERIC_INPUT_RESOURCE_IDS:
                    log(
                        f"[WARN] Generic input resource-id detected: {raw_resource_id}. "
                        f"Use parent field id: {parent_rid}"
                    )
                else:
                    log(f"[WARN] Input node without resource-id. Use parent fallback id: {parent_rid}")

                raw_resource_id = parent_rid
            else:
                component_short_type = component_type_name(component_type)
                raw_resource_id = (
                    f"no_id_{component_short_type}_"
                    f"{bounds['left']}_{bounds['top']}_{bounds['right']}_{bounds['bottom']}"
                )
                log(f"[WARN] Input node without usable resource-id. Use bounds fallback id: {raw_resource_id}")

        components.append(
            {
                "index": get_attr(node, "index", "0"),
                "type": component_type,
                "hint": get_attr(node, "hint", ""),
                "text": truncate_text(get_attr(node, "text", "")),
                "resource_id": raw_resource_id,
                "bounds": bounds,
                "resource_id_combined": "",
            }
        )

    id_counter: Dict[str, int] = {}
    for comp in components:
        rid = comp["resource_id"]
        id_counter[rid] = id_counter.get(rid, 0) + 1

    for comp in components:
        rid = comp["resource_id"]
        if id_counter[rid] > 1:
            b = comp["bounds"]
            comp["resource_id_combined"] = (
                f"{rid}__bounds_{b['left']}_{b['top']}_{b['right']}_{b['bottom']}"
            )
        else:
            comp["resource_id_combined"] = rid

    return components


def find_text_nodes(root: ET.Element) -> List[ET.Element]:
    nodes: List[ET.Element] = []

    for node in root.iter("node"):
        cls = get_attr(node, "class")
        text = get_attr(node, "text", "").strip()
        content_desc = get_attr(node, "content-desc", "").strip()

        if cls in TEXT_CLASSES and (text or content_desc):
            nodes.append(node)

    return nodes


def determine_relative_position(
    input_bounds: Dict[str, int],
    text_bounds: Dict[str, int],
    text_center: Tuple[float, float],
) -> Optional[str]:
    if (
        text_bounds["bottom"] <= input_bounds["top"]
        and input_bounds["left"] <= text_center[0] <= input_bounds["right"]
    ):
        return "top"

    if (
        text_bounds["top"] >= input_bounds["bottom"]
        and input_bounds["left"] <= text_center[0] <= input_bounds["right"]
    ):
        return "bottom"

    if (
        text_bounds["right"] <= input_bounds["left"]
        and input_bounds["top"] <= text_center[1] <= input_bounds["bottom"]
    ):
        return "left"

    if (
        text_bounds["left"] >= input_bounds["right"]
        and input_bounds["top"] <= text_center[1] <= input_bounds["bottom"]
    ):
        return "right"

    return None


def extract_adjacent_contexts(
    root: ET.Element,
    components: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Optional[Dict[str, Any]]]]:
    text_nodes = find_text_nodes(root)
    adjacent_contexts: Dict[str, Dict[str, Optional[Dict[str, Any]]]] = {}

    for comp in components:
        input_bounds = comp["bounds"]
        input_center = center(input_bounds)

        candidates: Dict[str, List[Dict[str, Any]]] = {
            "top": [],
            "bottom": [],
            "left": [],
            "right": [],
        }

        for text_node in text_nodes:
            text = get_attr(text_node, "text", "").strip()
            if not text:
                text = get_attr(text_node, "content-desc", "").strip()
            if not text:
                continue

            text_bounds = parse_bounds(get_attr(text_node, "bounds", ""))
            text_center = center(text_bounds)

            direction = determine_relative_position(input_bounds, text_bounds, text_center)
            if not direction:
                continue

            if direction in {"left", "right"}:
                distance = abs(input_center[0] - text_center[0])
            else:
                distance = abs(input_center[1] - text_center[1])

            candidates[direction].append(
                {
                    "text": text,
                    "distance": distance,
                }
            )

        adjacent: Dict[str, Optional[Dict[str, Any]]] = {}
        for direction, items in candidates.items():
            adjacent[direction] = sorted(items, key=lambda x: x["distance"])[0] if items else None

        adjacent_contexts[comp["resource_id_combined"]] = adjacent

    return adjacent_contexts


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def build_global_prompt(
    templates: Dict[str, str],
    app_name: str,
    activity: str,
    components: List[Dict[str, Any]],
) -> str:
    component_types = sorted(
        {
            component_type_name(comp.get("type", ""))
            for comp in components
            if comp.get("type")
        }
    )

    return templates["GloP"].format(
        app_name=app_name,
        input_count=len(components),
        activity=activity,
        component_types=", ".join(component_types) if component_types else "unknown input components",
    ).strip()


def build_component_prompts(
    templates: Dict[str, str],
    components: List[Dict[str, Any]],
) -> List[str]:
    prompts: List[str] = []

    for idx, comp in enumerate(components, start=1):
        prompts.append(
            templates["ComP"].format(
                component_order=ordinal(idx),
                component_type=component_type_name(comp.get("type", "")),
                resource_id=comp["resource_id_combined"],
                hint_text=comp.get("hint", ""),
                current_text=comp.get("text", ""),
            ).strip()
        )

    return prompts


def build_adjacent_prompts(
    templates: Dict[str, str],
    adjacent: Dict[str, Dict[str, Optional[Dict[str, Any]]]],
) -> List[str]:
    prompts: List[str] = []

    for comp_id, dirs in adjacent.items():
        for direction, info in dirs.items():
            if info and info.get("text"):
                prompts.append(
                    templates["AdjP"].format(
                        direction=direction,
                        component_id=comp_id,
                        text=info["text"],
                    ).strip()
                )

    return prompts


def build_restrictive_prompt(
    templates: Dict[str, str],
    components: List[Dict[str, Any]],
) -> str:
    resource_ids = [comp["resource_id_combined"] for comp in components]
    example = {rid: "generated_value" for rid in resource_ids}
    example_json = json.dumps(example, indent=2, ensure_ascii=False)

    resp_template = templates["ResP"].replace("valid text input", "invalid text input")

    return resp_template.format(example_json=example_json).strip()


def build_guiding_prompt(templates: Dict[str, str]) -> str:
    return templates["GuiP"].strip()


def serialize_prompt_list(items: List[str]) -> str:
    return json.dumps(items, ensure_ascii=False)


def row_exists(conn: pymysql.connections.Connection, page_id: int) -> bool:
    page_id = normalize_page_id(page_id)

    sql = """
        SELECT 1
        FROM t_invalid_prompt_context
        WHERE page_id = %s
        LIMIT 1
    """

    with conn.cursor() as cur:
        cur.execute(sql, (page_id,))
        return cur.fetchone() is not None


def ensure_row(conn: pymysql.connections.Connection, page_id: int) -> None:
    """
    page_id is INT PRIMARY KEY.

    If the row already exists, keep it.
    If it does not exist, insert an empty row with this page_id.
    """
    page_id = normalize_page_id(page_id)

    sql = """
        INSERT INTO t_invalid_prompt_context (page_id)
        VALUES (%s)
        ON DUPLICATE KEY UPDATE page_id = VALUES(page_id)
    """

    with conn.cursor() as cur:
        cur.execute(sql, (page_id,))


def update_db(
    conn: pymysql.connections.Connection,
    page_id: int,
    global_prompt: str,
    component_prompts: List[str],
    adjacent_prompts: List[str],
    restrictive_prompt: str,
    guiding_prompt: str,
    xml_text: str,
    image_64: str,
) -> None:
    page_id = normalize_page_id(page_id)

    ensure_row(conn, page_id)

    if not row_exists(conn, page_id):
        raise RuntimeError(
            f"Failed to create/find row in t_invalid_prompt_context for page_id={page_id}"
        )

    sql = """
        UPDATE t_invalid_prompt_context
        SET
            `global` = %s,
            component = %s,
            adjacent = %s,
            restrictive = %s,
            guiding = %s,
            xml = %s,
            image_64 = %s,
            update_time = CURRENT_TIMESTAMP
        WHERE page_id = %s
    """

    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                global_prompt,
                serialize_prompt_list(component_prompts),
                serialize_prompt_list(adjacent_prompts),
                restrictive_prompt,
                guiding_prompt,
                xml_text,
                image_64,
                page_id,
            ),
        )


def extract_and_save(
    page_id: int,
    serial: Optional[str],
    app_name: Optional[str],
    require_clickable: bool = False,
) -> None:
    page_id = normalize_page_id(page_id)
    app_name = app_name or f"page_{page_id}"

    conn = connect_db()

    try:
        templates = load_templates()
        device = u2.connect(serial) if serial else u2.connect()
        activity = get_current_activity(device, serial, page_id)

        log(f"[INFO] page_id={page_id}")
        log(f"[INFO] app_name={app_name}")
        log(f"[INFO] activity={activity}")

        log("[INFO] Dumping raw XML from current screen...")
        xml_text, root = dump_raw_xml(device)
        xml_path = save_xml(xml_text, page_id)
        log(f"[OK] XML saved: {xml_path}")

        log("[INFO] Saving screenshot from current screen...")
        screenshot_path = save_screenshot(serial, page_id)
        image_64 = image_to_base64(screenshot_path)
        log(f"[OK] Screenshot saved: {screenshot_path}, base64 length={len(image_64)}")

        screen_w, screen_h = get_screen_size(serial)
        log(f"[INFO] Screen size: {screen_w}x{screen_h}")

        components = extract_component_contexts(
            root=root,
            screen_width=screen_w,
            screen_height=screen_h,
            require_clickable=require_clickable,
        )

        if not components:
            raise RuntimeError(
                "No visible text-input components found. Please check whether the target page is open."
            )

        log(f"[OK] Found {len(components)} visible input components.")

        adjacent = extract_adjacent_contexts(root, components)

        global_prompt = build_global_prompt(templates, app_name, activity, components)
        component_prompts = build_component_prompts(templates, components)
        adjacent_prompts = build_adjacent_prompts(templates, adjacent)
        restrictive_prompt = build_restrictive_prompt(templates, components)
        guiding_prompt = build_guiding_prompt(templates)

        update_db(
            conn=conn,
            page_id=page_id,
            global_prompt=global_prompt,
            component_prompts=component_prompts,
            adjacent_prompts=adjacent_prompts,
            restrictive_prompt=restrictive_prompt,
            guiding_prompt=guiding_prompt,
            xml_text=xml_text,
            image_64=image_64,
        )

        conn.commit()

        log(f"[DONE] Updated t_invalid_prompt_context for page_id={page_id}")

        log("\n[Preview]")
        log(f"global: {global_prompt}")
        log(f"component: {component_prompts}")
        log(f"adjacent: {adjacent_prompts}")
        log(f"restrictive: {restrictive_prompt}")
        log(f"guiding: {guiding_prompt}")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-id", type=int, required=True)
    parser.add_argument("--serial", type=str, default=None)
    parser.add_argument("--app-name", type=str, default=None)
    parser.add_argument(
        "--require-clickable",
        action="store_true",
        help="Only keep clickable input components. Default keeps visible input components.",
    )

    args = parser.parse_args()

    extract_and_save(
        page_id=args.page_id,
        serial=args.serial,
        app_name=args.app_name,
        require_clickable=args.require_clickable,
    )


if __name__ == "__main__":
    main()