"""Fail-closed Android adapter for E2-structure-v1.

The adapter is a JSON-in/JSON-out command.  Baseline reset and within-attempt
recovery are case-specific argv contracts, while input binding, action
execution, UI assertions, and evidence capture use current UIAutomator state.
Coordinate taps are accepted only when the action freezes and verifies the
foreground package and exact screen dimensions before tapping.  This narrow
escape hatch is needed for App-owned WebView canvases whose controls are not
represented in the accessibility hierarchy.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SELECTOR_KEYS = {
    "package", "class", "resource-id", "resource-id-prefix", "text", "content-desc", "hint",
    "enabled", "instance", "unique", "ancestor-resource-id", "descendant-text",
}
UI_SELECTOR_KEYS = {
    "package", "class", "resource-id", "text", "content-desc", "enabled",
}
ACCESSIBILITY_EDIT_TEXT_RE = re.compile(
    r"^(?:(?P<value>.*), )?insertion at (?:beginning|end|[0-9]+) of text\."
    r".*edit box\.Edit box, double tap to edit text, double tap to long press\.$"
)


class AppInputRejected(RuntimeError):
    """The App-owned editor refused the requested value after real key events."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def logical_binding_value(observed: str, hint: str) -> str:
    if hint and observed == hint:
        return ""
    match = ACCESSIBILITY_EDIT_TEXT_RE.fullmatch(observed)
    if match is not None:
        return match.group("value") or ""
    return observed


def accessibility_rendered_value(value: str) -> str:
    """Match Android's hierarchy rendering for supplementary Unicode.

    The Android 13 UIAutomator hierarchy used by the experiment serializes
    each UTF-16 surrogate of a non-BMP character as ``.``.  This is visible in
    Gboard's own emoji keys as well as App EditTexts, even when the Unicode
    value was delivered through the base64 input method.  Preserve exact
    matching for every BMP character and model only that observed transport
    normalization for supplementary code points.
    """
    return "".join(".." if ord(character) > 0xFFFF else character for character in value)


def binding_value_matches(value: str, observed: str, hint: str) -> bool:
    # A numeric editor may use its legal zero value as the visual hint.  Once
    # the runner explicitly writes that same value, the exact text is stronger
    # evidence than the hint heuristic used to recognise an untouched field.
    if observed == value:
        return True
    observed = logical_binding_value(observed, hint)
    if observed == value:
        return True
    rendered = accessibility_rendered_value(value)
    if rendered != value and observed == rendered:
        return True
    # Material-style fields may expose ``label\nvalue`` as one accessibility
    # text while still storing exactly the submitted value in the editor.
    if "\n" in observed and observed.rsplit("\n", 1)[1].strip() == value:
        return True
    phone_characters = re.compile(r"^[+()\d .-]+$")
    if not (
        bool(value)
        and bool(observed)
        and phone_characters.fullmatch(value) is not None
        and phone_characters.fullmatch(observed) is not None
    ):
        return False
    expected_digits = "".join(re.findall(r"\d", value))
    observed_digits = "".join(re.findall(r"\d", observed))
    return expected_digits == observed_digits or (
        value.startswith("+")
        and 10 <= len(observed_digits) <= 12
        and 1 <= len(expected_digits) - len(observed_digits) <= 3
        and expected_digits.endswith(observed_digits)
    ) or (
        # International phone widgets often render a frozen country prefix
        # that was not part of the submitted national number.
        1 <= len(observed_digits) - len(expected_digits) <= 3
        and observed_digits.endswith(expected_digits)
    )


def masked_phone_suffix_matches(value: str, observed: str, hint: str) -> bool:
    """Accept a calibrated phone mask that keeps a national-number suffix.

    This is deliberately separate from the default binding matcher.  A few
    country-prefixed phone editors accept a longer digit sequence, retain the
    final national-number digits, and expose their own punctuation.  Only a
    frozen per-binding input strategy may opt into that behavior; ordinary
    numeric and text fields remain exact.
    """
    if binding_value_matches(value, observed, hint):
        return True
    observed = logical_binding_value(observed, hint)
    phone_characters = re.compile(r"^[+()\d .-]+$")
    if not (
        bool(value)
        and bool(observed)
        and phone_characters.fullmatch(value) is not None
        and phone_characters.fullmatch(observed) is not None
    ):
        return False
    expected_digits = "".join(re.findall(r"\d", value))
    observed_digits = "".join(re.findall(r"\d", observed))
    return (
        10 <= len(observed_digits) <= 12
        and 1 <= len(expected_digits) - len(observed_digits) <= 3
        and expected_digits.endswith(observed_digits)
    )


def app_input_limit_reached(
    value: str, observed: str, hint: str, root: ET.Element,
) -> bool:
    """Recognise an App-owned length cap without accepting a partial write."""
    logical_observed = logical_binding_value(observed, hint)
    if (
        not logical_observed
        or logical_observed == value
        or not value.startswith(logical_observed)
    ):
        return False
    counter = re.compile(r"(?<!\d)(\d+)\s*(?:/|of)\s*(\d+)(?!\d)", re.IGNORECASE)
    for node in root.iter("node"):
        for attribute in ("text", "content-desc"):
            match = counter.search(node.get(attribute, ""))
            if match is None:
                continue
            current, maximum = map(int, match.groups())
            if current == maximum == len(logical_observed):
                return True
    return False


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _adb_uiautomator_hierarchy(serial: str) -> str:
    """Fallback for legacy dialogs omitted by the uiautomator2 HTTP dump."""
    remote = "/sdcard/codex_e2_window_dump.xml"
    last_error = "native UI hierarchy fallback failed"
    for attempt in range(3):
        dumped = subprocess.run(
            ["adb", "-s", serial, "shell", "uiautomator", "dump", remote],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False,
        )
        if dumped.returncode == 0:
            captured = subprocess.run(
                ["adb", "-s", serial, "shell", "cat", remote],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10, check=False,
            )
            if captured.returncode == 0 and captured.stdout.strip():
                try:
                    ET.fromstring(captured.stdout)
                    return captured.stdout
                except ET.ParseError:
                    last_error = "native UI hierarchy fallback returned invalid XML"
            else:
                last_error = "native UI hierarchy fallback returned no XML"
        else:
            detail = (dumped.stderr or dumped.stdout).strip().replace("\n", " ")
            last_error = (
                "native UI hierarchy fallback command failed"
                f" (exit={dumped.returncode}, detail={detail[:300] or 'none'})"
            )
        if attempt < 2:
            time.sleep(1)
    raise RuntimeError(last_error)


def _adb_foreground_app(serial: str) -> dict[str, Any]:
    """Read foreground state through bounded adb subprocesses.

    ``uiautomator2.app_current`` ultimately performs an adb socket read without
    a deadline.  A snapshot restore can leave that socket waiting forever even
    though ordinary adb commands still work, so formal evidence capture must
    not depend on it.
    """
    match: re.Match[str] | None = None
    for attempt in range(5):
        window = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "window"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False,
        )
        match = re.search(
            r"mCurrentFocus=.*?\bu\d+\s+([^\s/]+)/([^\s}]+)",
            window.stdout if window.returncode == 0 else "",
        )
        if match is None:
            activities = subprocess.run(
                ["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False,
            )
            match = re.search(
                r"(?:mResumedActivity:|topResumedActivity=).*?\bu\d+\s+"
                r"([^\s/]+)/([^\s}]+)",
                activities.stdout if activities.returncode == 0 else "",
            )
        if match is not None:
            break
        if attempt < 4:
            time.sleep(0.5)
    if match is None:
        raise RuntimeError("foreground App is unavailable from bounded Android system state")
    package, activity = match.groups()
    pid_result = subprocess.run(
        ["adb", "-s", serial, "shell", "pidof", package],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, check=False,
    )
    tokens = pid_result.stdout.strip().split() if pid_result.returncode == 0 else []
    pid = int(tokens[0]) if tokens and tokens[0].isdigit() else None
    return {"package": package, "activity": activity, "pid": pid}


def _selector_kwargs(selector: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(selector, dict) or set(selector) - SELECTOR_KEYS:
        raise ValueError("binding selector contains unsupported fields")
    if not isinstance(selector.get("package"), str) or not selector["package"]:
        raise ValueError("binding selector requires a non-empty package")
    if not isinstance(selector.get("class"), str) or not selector["class"]:
        raise ValueError("binding selector requires a non-empty class")
    anchors = {
        key for key in (
            "resource-id", "resource-id-prefix", "text", "content-desc", "hint",
            "ancestor-resource-id", "descendant-text",
        )
        if selector.get(key)
    }
    if not anchors and selector.get("unique") is not True:
        raise ValueError("binding selector requires a stable anchor or an explicit unique-node contract")
    for key in (
        "resource-id", "resource-id-prefix", "text", "content-desc", "hint",
        "ancestor-resource-id", "descendant-text",
    ):
        if key in selector and (not isinstance(selector[key], str) or not selector[key]):
            raise ValueError("binding selector string fields must be non-empty")
    if "enabled" in selector and not isinstance(selector["enabled"], bool):
        raise ValueError("binding selector enabled must be boolean")
    if "instance" in selector and (
        not isinstance(selector["instance"], int) or isinstance(selector["instance"], bool)
        or selector["instance"] < 0
    ):
        raise ValueError("binding selector instance must be a non-negative integer")
    if "unique" in selector and selector["unique"] is not True:
        raise ValueError("binding selector unique must be boolean true")
    mapping = {
        "package": "packageName",
        "class": "className",
        "resource-id": "resourceId",
        "text": "text",
        "content-desc": "description",
        "enabled": "enabled",
        "instance": "instance",
    }
    return {mapping[key]: value for key, value in selector.items() if key in mapping}


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def _binding_xpath(selector: dict[str, Any], *, force: bool = False) -> str | None:
    _selector_kwargs(selector)
    if not force and not {
        "resource-id-prefix", "hint", "ancestor-resource-id", "descendant-text",
    } & set(selector):
        return None
    attributes = {
        "package": "package",
        "resource-id": "resource-id",
        "text": "text",
        "content-desc": "content-desc",
        "hint": "hint",
    }
    # uiautomator2's XPath source drops the XML ``class`` attribute (it is
    # available only as ``className`` in the object info mapping), so including
    # @class in an XPath predicate deterministically yields zero matches.  The
    # full class constraint remains mandatory in _selector_kwargs and is
    # rechecked against the unmodified hierarchy by binding_nodes after writes.
    predicates = [
        f"@{attributes[key]}={_xpath_literal(value)}"
        for key, value in selector.items()
        if key in attributes
    ]
    if "resource-id-prefix" in selector:
        predicates.append(
            "starts-with(@resource-id, "
            f"{_xpath_literal(selector['resource-id-prefix'])})"
        )
    if "enabled" in selector:
        predicates.append(f"@enabled='{str(selector['enabled']).lower()}'")
    if "ancestor-resource-id" in selector:
        predicates.append(
            "ancestor::*[@resource-id="
            f"{_xpath_literal(selector['ancestor-resource-id'])}]"
        )
    if "descendant-text" in selector:
        predicates.append(
            ".//*[@text="
            f"{_xpath_literal(selector['descendant-text'])}]"
        )
    expression = f"//*[{ ' and '.join(predicates) }]"
    if "instance" in selector:
        expression = f"({expression})[{selector['instance'] + 1}]"
    return expression


def binding_nodes(root: ET.Element, selector: dict[str, Any]) -> list[ET.Element]:
    """Resolve a binding selector against captured XML using runtime semantics."""
    _selector_kwargs(selector)
    parent = {child: item for item in root.iter() for child in item}
    direct_keys = ("package", "class", "resource-id", "text", "content-desc", "hint")
    matches: list[ET.Element] = []
    for node in root.iter("node"):
        if any(node.get(key, "") != selector[key] for key in direct_keys if key in selector):
            continue
        if "resource-id-prefix" in selector and not node.get("resource-id", "").startswith(
            selector["resource-id-prefix"]
        ):
            continue
        if "enabled" in selector and node.get("enabled", "") != str(selector["enabled"]).lower():
            continue
        ancestor_id = selector.get("ancestor-resource-id")
        if ancestor_id:
            current = parent.get(node)
            found = False
            while current is not None:
                if current.get("resource-id") == ancestor_id:
                    found = True
                    break
                current = parent.get(current)
            if not found:
                continue
        descendant_text = selector.get("descendant-text")
        if descendant_text and not any(
            child is not node and child.get("text") == descendant_text
            for child in node.iter("node")
        ):
            continue
        matches.append(node)
    if "instance" in selector:
        index = selector["instance"]
        return [matches[index]] if index < len(matches) else []
    return matches


class _XPathBindingObject:
    def __init__(self, selector: Any, count: int, device: Any) -> None:
        self.selector = selector
        self.count = count
        self.device = device

    def click(self) -> None:
        self.selector.click()

    def long_click(self) -> None:
        self.selector.long_click()

    def clear_text(self) -> None:
        self.device.clear_text()

    def set_text(self, value: str) -> None:
        self.device.send_keys(value)


class _CurrentNodeBindingObject:
    """Operate a unique node at coordinates derived from the current hierarchy."""

    def __init__(self, device: Any, node: ET.Element) -> None:
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
        if match is None:
            raise RuntimeError("unique current binding has no usable bounds")
        left, top, right, bottom = map(int, match.groups())
        if right <= left or bottom <= top:
            raise RuntimeError("unique current binding has invalid bounds")
        self.device = device
        self.x = (left + right) // 2
        self.y = (top + bottom) // 2
        self.count = 1
        mapping = {
            "package": "packageName",
            "class": "className",
            "resource-id": "resourceId",
            "content-desc": "description",
        }
        self.native_kwargs = {
            runtime: node.get(source, "")
            for source, runtime in mapping.items()
            if node.get(source, "")
        }

    def _native_object(self) -> Any | None:
        if "resourceId" not in self.native_kwargs:
            return None
        obj = self.device(**self.native_kwargs)
        return obj if int(getattr(obj, "count", 0)) == 1 else None

    def click(self) -> None:
        self.device.click(self.x, self.y)

    def long_click(self) -> None:
        self.device.long_click(self.x, self.y)

    def clear_text(self) -> None:
        obj = self._native_object()
        if obj is not None:
            obj.clear_text()
            return
        self.device.clear_text()

    def set_text(self, value: str) -> None:
        obj = self._native_object()
        if obj is not None:
            obj.set_text(value)
            return
        self.device.send_keys(value)


def _ui_selector_kwargs(selector: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(selector, dict) or set(selector) - UI_SELECTOR_KEYS:
        raise ValueError("UI selector contains unsupported fields")
    if not isinstance(selector.get("package"), str) or not selector["package"]:
        raise ValueError("UI selector requires a non-empty package")
    anchors = {key for key in ("resource-id", "text", "content-desc") if selector.get(key)}
    string_fields = {"package", "class", "resource-id", "text", "content-desc"}
    if (
        not anchors
        or any(
            key in selector and (not isinstance(selector[key], str) or not selector[key])
            for key in string_fields
        )
        or "enabled" in selector and not isinstance(selector["enabled"], bool)
    ):
        raise ValueError("UI selector requires a non-empty resource-id, text, or content-desc anchor")
    mapping = {
        "package": "packageName",
        "class": "className",
        "resource-id": "resourceId",
        "text": "text",
        "content-desc": "description",
        "enabled": "enabled",
    }
    return {mapping[key]: value for key, value in selector.items()}


def _run_contract_command(argv: list[str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise ValueError("case command must be a non-empty argv list")
    completed = subprocess.run(
        argv,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail[-2000:]}" if detail else ""
        raise RuntimeError(
            f"case command exited with code {completed.returncode}{suffix}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        # uiautomator2 can print first-connect helper installation progress to
        # stdout before the reset/recovery command emits its JSON receipt.  As
        # at the outer environment boundary, accept only a complete final JSON
        # object; prose or partial JSON still fails closed.
        value = None
        for line in reversed(completed.stdout.splitlines()):
            candidate = line.strip()
            if not (candidate.startswith("{") and candidate.endswith("}")):
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                value = parsed
                break
        if value is None:
            raise RuntimeError("case command did not return one JSON object") from exc
    if not isinstance(value, dict):
        raise RuntimeError("case command result must be an object")
    return value


def assertion_in_hierarchy(hierarchy: str, assertion: dict[str, Any]) -> bool:
    """Evaluate one frozen assertion against one atomic hierarchy capture."""
    mode = assertion.get("mode")
    targets = assertion.get("targets")
    if mode not in {"all", "any"} or not isinstance(targets, list) or not targets:
        raise ValueError("invalid UI assertion")
    root = ET.fromstring(hierarchy)
    checks = []
    for target in targets:
        selector = target.get("selector")
        expected = target.get("exists")
        if not isinstance(selector, dict) or not isinstance(expected, bool):
            raise ValueError("invalid assertion target")
        binding_selector = bool(set(selector) - UI_SELECTOR_KEYS)
        if binding_selector:
            _selector_kwargs(selector)
            nodes = binding_nodes(root, selector)
        else:
            _ui_selector_kwargs(selector)
            nodes = [
                node for node in root.iter("node")
                if all(
                    node.get(key) == (
                        str(value).lower() if key == "enabled" else value
                    )
                    for key, value in selector.items()
                )
            ]
        check = bool(nodes) is expected
        if "attribute" in target or "equals" in target:
            attribute = target.get("attribute")
            if attribute == "text_equals_hint":
                if target.get("equals") is not True:
                    raise ValueError("text_equals_hint assertions require boolean true")
                check = (
                    check and len(nodes) == 1
                    and bool(nodes[0].get("hint", ""))
                    and nodes[0].get("text", "") == nodes[0].get("hint", "")
                )
            elif attribute == "text_non_empty":
                if target.get("equals") is not True:
                    raise ValueError("text_non_empty assertions require boolean true")
                check = (
                    check and len(nodes) == 1
                    and bool(logical_binding_value(
                        nodes[0].get("text", ""), nodes[0].get("hint", ""),
                    ))
                )
            elif attribute not in {"text", "hint", "content-desc"} or not isinstance(target.get("equals"), str):
                raise ValueError("attribute assertions require a supported attribute and string equals")
            else:
                observed = nodes[0].get(attribute, "") if len(nodes) == 1 else ""
                if attribute == "text" and len(nodes) == 1:
                    observed = logical_binding_value(observed, nodes[0].get("hint", ""))
                check = check and len(nodes) == 1 and observed == target["equals"]
        checks.append(check)
    return all(checks) if mode == "all" else any(checks)


class AndroidDriver:
    def __init__(
        self, serial: str, *, device: Any | None = None,
        prefer_adb_hierarchy: bool = False,
        frozen_hierarchy: str | None = None,
    ) -> None:
        self.serial = serial
        self.prefer_adb_hierarchy = prefer_adb_hierarchy
        self.frozen_hierarchy = frozen_hierarchy
        self.hierarchy_source = (
            "frozen_initial_xml" if frozen_hierarchy is not None else
            "adb_uiautomator" if prefer_adb_hierarchy else
            "live_uiautomator2"
        )
        # A conditional action can deliberately stop a calibrated sequence
        # without raising.  Preserve a small, non-hierarchy receipt so the
        # outer capture adapter can finish the turn without re-traversing an
        # unstable accessibility tree merely to rediscover why it stopped.
        self.last_action_stop: dict[str, Any] | None = None
        if device is None:
            import uiautomator2 as u2

            device = u2.connect(serial)
        self.device = device

    def hierarchy(self) -> str:
        if self.frozen_hierarchy is not None:
            return self.frozen_hierarchy
        if self.serial != "fake" and self.prefer_adb_hierarchy:
            return _adb_uiautomator_hierarchy(self.serial)
        value = ""
        package: str | None = None
        last_dump_error: Exception | None = None
        for attempt in range(3):
            try:
                value = self.device.dump_hierarchy(compressed=False)
            except Exception as exc:
                # Snapshot loads can leave the uiautomator instrumentation
                # connected while AccessibilityServiceInfo is momentarily
                # unavailable.  Treat that transport condition separately
                # from an App/Oracle failure and restart the instrumentation
                # before the bounded retry.
                last_dump_error = exc
                if attempt < 2:
                    reset = getattr(self.device, "reset_uiautomator", None)
                    if callable(reset):
                        try:
                            reset()
                        except Exception:
                            pass
                    time.sleep(1)
                    continue
                break
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError("empty UI hierarchy")
            root = ET.fromstring(value)
            current = self.current_app()
            package = current.get("package") if isinstance(current, dict) else None
            app_nodes = (
                [node for node in root.iter("node") if node.get("package") == package]
                if isinstance(package, str) and package else []
            )
            semantic_app_nodes = [
                node for node in app_nodes
                if any(node.get(key) for key in ("resource-id", "text", "content-desc"))
            ]
            if not isinstance(package, str) or not package or semantic_app_nodes:
                return value
            if attempt < 2:
                # A just-launched Activity can briefly expose only its root
                # window.  Re-read the primary hierarchy before invoking the
                # heavier native dumper, whose process can be killed while
                # the window is still settling.
                time.sleep(0.5)
        if not value and self.serial == "fake" and last_dump_error is not None:
            raise last_dump_error
        if self.serial != "fake":
            value = _adb_uiautomator_hierarchy(self.serial)
        return value

    def current_app(self) -> dict[str, Any]:
        value = (
            self.device.app_current()
            if self.serial == "fake"
            else _adb_foreground_app(self.serial)
        )
        if not isinstance(value, dict) or not value.get("package"):
            raise RuntimeError("foreground App is unavailable")
        return value

    def app_view_root_count(self, package: str) -> int:
        """Return the AUT's current ViewRootImpl count without UI traversal."""
        if not isinstance(package, str) or not package:
            raise ValueError("view-root diagnostics require an App package")
        if self.serial == "fake":
            value = getattr(self.device, "view_root_count", 1)
            if type(value) is not int or value < 0:
                raise RuntimeError("fake App view-root count is invalid")
            return value
        completed = subprocess.run(
            ["adb", "-s", self.serial, "shell", "dumpsys", "meminfo", package],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False,
        )
        match = re.search(r"ViewRootImpl:\s*(\d+)", completed.stdout)
        if completed.returncode != 0 or match is None:
            raise RuntimeError("App view-root count is unavailable")
        return int(match.group(1))

    def window_size(self) -> tuple[int, int]:
        if self.serial == "fake":
            width, height = self.device.window_size()
            return int(width), int(height)
        completed = subprocess.run(
            ["adb", "-s", self.serial, "shell", "wm", "size"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
        matches = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", completed.stdout)
        if completed.returncode != 0 or not matches:
            raise RuntimeError("Android window size is unavailable")
        width, height = matches[-1]
        return int(width), int(height)

    def state(self) -> tuple[str, str, dict[str, Any]]:
        hierarchy = self.hierarchy()
        current = self.current_app()
        # Full accessibility XML contains clocks, loading indicators, animated
        # lists, and other content that can change between the execution and
        # recovery subprocesses without a navigation/reset.  Continuity is
        # therefore the stable foreground component; full XML integrity stays
        # in the separately hashed evidence files and recovery re-verifies the
        # frozen target-page/input assertions.
        value = {
            "foreground": {
                "package": current.get("package"),
                "activity": current.get("activity"),
            }
        }
        return sha256_bytes(canonical_json(value).encode("utf-8")), hierarchy, current

    def object(self, selector: dict[str, Any], *, binding: bool = False) -> Any:
        nodes: list[ET.Element] = []
        if binding:
            nodes = binding_nodes(ET.fromstring(self.hierarchy()), selector)
            if len(nodes) == 1:
                # Native selectors can count hidden React/Compose nodes or
                # stall while waiting for an object that is already present
                # in the current accessibility tree.  Prefer that uniquely
                # observed live node and derive a fresh coordinate for this
                # operation only.  Boundless fake/test nodes fall through to
                # the native selector implementation.
                try:
                    return _CurrentNodeBindingObject(self.device, nodes[0])
                except RuntimeError:
                    pass
            # Class-only bindings explicitly freeze uniqueness in the current
            # App hierarchy.  The uiautomator2 XPath source omits ``class``;
            # falling through would therefore broaden this selector to every
            # enabled node in the package and can type into an unrelated view.
            anchors = {
                key for key in (
                    "resource-id", "resource-id-prefix", "text", "content-desc", "hint",
                    "ancestor-resource-id", "descendant-text",
                )
                if selector.get(key)
            }
            if selector.get("unique") is True and not anchors:
                raise RuntimeError(f"dynamic selector matched {len(nodes)} nodes")
        expression = _binding_xpath(selector) if binding else None
        if expression is not None:
            xpath = self.device.xpath(expression)
            obj = _XPathBindingObject(xpath, len(xpath.all()), self.device)
        else:
            kwargs = _selector_kwargs(selector) if binding else _ui_selector_kwargs(selector)
            obj = self.device(**kwargs)
        count = int(getattr(obj, "count", 0))
        if binding and count == 0 and expression is None and hasattr(self.device, "xpath"):
            xpath = self.device.xpath(_binding_xpath(selector, force=True))
            obj = _XPathBindingObject(xpath, len(xpath.all()), self.device)
            count = obj.count
        if count != 1:
            raise RuntimeError(f"dynamic selector matched {count} nodes")
        return obj

    def _ime_visible(self, package: str) -> bool:
        root = ET.fromstring(self.hierarchy())
        return any(
            node.get("package") != package
            and (
                "inputmethod" in node.get("package", "").lower()
                or "inputmethod" in node.get("class", "").lower()
                or node.get("package") == "com.github.uiautomator"
            )
            for node in root.iter("node")
        )

    def _maybe_object(self, selector: dict[str, Any], *, binding: bool) -> Any | None:
        try:
            return self.object(selector, binding=binding)
        except RuntimeError as exc:
            if str(exc) != "dynamic selector matched 0 nodes":
                raise
            return None

    def _scroll_to_binding(self, selector: dict[str, Any]) -> bool:
        """Bring a virtualized binding into the hierarchy without coordinates."""
        package = selector["package"]
        target_mapping = {
            "resource-id": "resourceId",
            "text": "text",
            "content-desc": "description",
        }
        target = {
            runtime: selector[source]
            for source, runtime in target_mapping.items()
            if selector.get(source)
        }
        if not target:
            return False
        scrollables = self.device(packageName=package, scrollable=True)
        count = int(getattr(scrollables, "count", 0))
        for index in range(count):
            container = self.device(
                packageName=package, scrollable=True, instance=index,
            )
            try:
                if container.scroll.to(**target):
                    return True
            except Exception:
                continue
        return False

    def _viewport_sweep_to_object(
        self, selector: dict[str, Any], *, binding: bool,
    ) -> Any | None:
        """Find fields in scrollable Compose trees that expose no scrollable node."""
        if self.serial == "fake" or not hasattr(self.device, "swipe"):
            return None
        try:
            size = self.window_size()
            width, height = int(size[0]), int(size[1])
        except Exception:
            width, height = 1080, 1920
        # Search down first (the common form-fill direction), then restore and
        # search upward.  Every successful result is still checked for exact
        # selector uniqueness by _maybe_object.
        gestures = [(0.76, 0.30), (0.76, 0.30), (0.30, 0.76)] * 2
        for start_ratio, end_ratio in gestures:
            try:
                self.device.swipe(
                    width // 2, int(height * start_ratio),
                    width // 2, int(height * end_ratio), 0.25,
                )
            except Exception:
                return None
            time.sleep(0.35)
            resolved = self._maybe_object(selector, binding=binding)
            if resolved is not None:
                return resolved
        return None

    def binding_object(self, selector: dict[str, Any]) -> Any:
        """Resolve a binding, scrolling app-owned containers when virtualized."""
        resolved = self._maybe_object(selector, binding=True)
        if resolved is not None:
            return resolved
        package = selector["package"]
        self._scroll_to_binding(selector)
        resolved = self._maybe_object(selector, binding=True)
        if resolved is not None:
            return resolved
        if self._ime_visible(package):
            self.device.press("back")
            time.sleep(0.5)
            self._scroll_to_binding(selector)
            resolved = self._maybe_object(selector, binding=True)
            if resolved is not None:
                return resolved
        # A restored snapshot can report the target Activity before a React
        # Native/Compose subtree has repopulated Accessibility.  Give that
        # subtree a small bounded settle window; absence after the window is
        # still a hard binding failure rather than an implicit skip.
        if self.serial != "fake":
            for _ in range(6):
                time.sleep(0.5)
                resolved = self._maybe_object(selector, binding=True)
                if resolved is not None:
                    return resolved
            resolved = self._viewport_sweep_to_object(selector, binding=True)
            if resolved is not None:
                return resolved
        return self.object(selector, binding=True)

    def action_object(self, selector: dict[str, Any]) -> Any:
        """Resolve an action target after stable-selector scrolling if needed."""
        package = selector["package"]
        binding = bool(
            {
                "resource-id-prefix", "hint", "enabled", "instance", "unique",
                "ancestor-resource-id", "descendant-text",
            }
            & set(selector)
        )
        resolved = self._maybe_object(selector, binding=binding)
        if resolved is not None:
            # Android may keep an App-owned button in the hierarchy even when
            # the IME physically covers its coordinates.  Clicking that stale
            # object routes the event to the keyboard.  Hide the IME and
            # resolve the target again before executing the action.
            if not self._ime_visible(package):
                return resolved
            self.device.press("back")
            time.sleep(0.5)
            resolved = self._maybe_object(selector, binding=binding)
            if resolved is not None:
                return resolved
        if "resource-id-prefix" in selector:
            # Prefix selectors are reserved for visible dynamic result rows.
            # If no row exists, swiping the current error/empty screen can be
            # interpreted as a tap by unstable Compose surfaces and activate
            # unrelated retry/login flows.  Fail closed after hiding the IME;
            # the action's existing if_missing contract decides whether this
            # absence is a semantic stop or a calibration error.
            if self._ime_visible(package):
                self.device.press("back")
                time.sleep(0.5)
                resolved = self._maybe_object(selector, binding=binding)
                if resolved is not None:
                    return resolved
            return self.object(selector, binding=binding)
        self._scroll_to_binding(selector)
        resolved = self._maybe_object(selector, binding=binding)
        if resolved is not None:
            return resolved
        if self._ime_visible(package):
            self.device.press("back")
            time.sleep(0.5)
            self._scroll_to_binding(selector)
            resolved = self._maybe_object(selector, binding=binding)
            if resolved is not None:
                return resolved
        resolved = self._viewport_sweep_to_object(selector, binding=binding)
        if resolved is not None:
            return resolved
        return self.object(selector, binding=binding)

    def set_binding_text(
        self, selector: dict[str, Any], value: str, *,
        allow_masked_phone_suffix: bool = False,
    ) -> None:
        """Interact through freshly relocated objects as virtualized views can move."""
        value_matches = (
            masked_phone_suffix_matches
            if allow_masked_phone_suffix else binding_value_matches
        )
        last_error: Exception | None = None
        for attempt in range(3):
            # Hide the previous editor before scrolling.  Otherwise a newly
            # found field can be only a few pixels above the IME and its click
            # is routed to the still-focused previous React Native editor.
            current_nodes = binding_nodes(
                ET.fromstring(self.hierarchy()), selector,
            )
            target_focused = (
                len(current_nodes) == 1
                and current_nodes[0].get("focused") == "true"
            )
            if self._ime_visible(selector["package"]) and not target_focused:
                self.device.press("back")
                time.sleep(0.5)
                current_nodes = binding_nodes(
                    ET.fromstring(self.hierarchy()), selector,
                )
            if (
                len(current_nodes) == 1
                and self.serial != "fake"
                and not target_focused
            ):
                match = re.fullmatch(
                    r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                    current_nodes[0].get("bounds", ""),
                )
                if match is not None:
                    _, top, _, bottom = map(int, match.groups())
                    try:
                        width, height = self.window_size()
                    except Exception:
                        width, height = 1080, 1920
                    # Editors at the viewport edge can be geometrically
                    # present while their center is covered by a sticky CTA,
                    # navigation bar, or keyboard inset.  Reposition them with
                    # a screen-relative scroll before deriving the live click.
                    if bottom > int(height * 0.88):
                        self.device.swipe(
                            width // 2, int(height * 0.76),
                            width // 2, int(height * 0.42), 0.25,
                        )
                        time.sleep(0.35)
                        current_nodes = binding_nodes(
                            ET.fromstring(self.hierarchy()), selector,
                        )
                    elif top < int(height * 0.08):
                        self.device.swipe(
                            width // 2, int(height * 0.36),
                            width // 2, int(height * 0.62), 0.25,
                        )
                        time.sleep(0.35)
                        current_nodes = binding_nodes(
                            ET.fromstring(self.hierarchy()), selector,
                        )
            if len(current_nodes) == 1:
                observed = current_nodes[0].get("text", "")
                hint = current_nodes[0].get("hint", "")
                if value_matches(value, observed, hint):
                    # Avoid reopening pickers or re-triggering formatting when
                    # the restored snapshot already contains the exact arm
                    # value.  This is especially important for idempotent
                    # baseline controls with read-only/date fields.
                    return
                if current_nodes[0].get("enabled") == "false":
                    raise AppInputRejected(
                        "App-owned binding is read-only and differs from the requested value: "
                        f"expected={value!r}, observed={observed!r}, hint={hint!r}"
                    )
            obj = self.binding_object(selector)
            try:
                obj.click()
                time.sleep(0.3)
                nodes = binding_nodes(ET.fromstring(self.hierarchy()), selector)
                if len(nodes) != 1:
                    # Compose/WebView and RecyclerView forms can replace the
                    # accessibility node as focus is granted.  Relocate once;
                    # the exact post-write assertion below remains mandatory.
                    obj = self.binding_object(selector)
                    obj.click()
                    time.sleep(0.3)
                obj.clear_text()
                obj.set_text(value)
                time.sleep(0.2)
                written_root = ET.fromstring(self.hierarchy())
                written = binding_nodes(written_root, selector)
                if len(written) != 1:
                    raise RuntimeError(
                        f"binding post-write check matched {len(written)} nodes"
                    )
                if written[0].get("password") != "true":
                    observed = written[0].get("text", "")
                    hint = written[0].get("hint", "")
                    if not value_matches(value, observed, hint):
                        if app_input_limit_reached(
                            value, observed, hint, written_root,
                        ):
                            raise AppInputRejected(
                                "App-owned editor truncated the value at its visible limit: "
                                f"requested_length={len(value)}, accepted_length="
                                f"{len(logical_binding_value(observed, hint))}"
                            )
                        if (
                            attempt == 2
                            and bool(value)
                            and logical_binding_value(observed, hint) == ""
                        ):
                            raise AppInputRejected(
                                "App-owned editor refused a non-empty value after "
                                "three verified write attempts"
                            )
                        raise RuntimeError(
                            "binding post-write value mismatch: "
                            f"expected={value!r}, observed={observed!r}, hint={hint!r}"
                        )
                return
            except Exception as exc:
                if type(exc).__name__ not in {
                    "UiObjectNotFoundError",
                    "RuntimeError",
                    # uiautomator2 can briefly lose its helper IME after a
                    # snapshot reload, and adbutils can time out on an
                    # otherwise healthy emulator.  A field write is
                    # idempotent here: every retry re-resolves the exact
                    # binding, clears it, writes it, and verifies the live
                    # value before returning.
                    "InputIMEError",
                    "AdbTimeout",
                }:
                    raise
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5)
        assert last_error is not None
        raise last_error

    def set_binding_text_adb_key_events(
        self, selector: dict[str, Any], value: str,
    ) -> None:
        """Type through Android key events after a verified binding clear.

        Some Compose phone fields visually accept uiautomator ``set_text`` but
        do not update their internal validator state. This narrow strategy
        keeps dynamic binding/after-write checks while dispatching the new
        value as actual Android text input events.
        """
        if self.serial == "fake":
            self.set_binding_text(selector, value)
            return
        if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
            raise ValueError("adb_key_events supports printable ASCII input only")
        if "%" in value:
            raise ValueError("adb_key_events does not support percent characters")
        obj = self.binding_object(selector)
        obj.click()
        time.sleep(0.2)
        obj.clear_text()
        if value:
            completed = subprocess.run(
                [
                    "adb", "-s", self.serial, "shell", "input", "text",
                    value.replace(" ", "%s"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("adb_key_events input command failed")
        time.sleep(0.3)
        written = binding_nodes(ET.fromstring(self.hierarchy()), selector)
        if len(written) != 1:
            raise RuntimeError(
                f"binding post-write check matched {len(written)} nodes"
            )
        if written[0].get("password") != "true":
            observed = written[0].get("text", "")
            hint = written[0].get("hint", "")
            if not binding_value_matches(value, observed, hint):
                raise AppInputRejected(
                    "binding post-write value mismatch: "
                    f"expected={value!r}, observed={observed!r}, hint={hint!r}"
                )

    def set_binding_text_adb_shell(
        self, selector: dict[str, Any], value: str,
    ) -> None:
        """Tap, clear, and type without the uiautomator HTTP action channel.

        A small number of legacy Apps expose a valid hierarchy while their
        UiObject action endpoint hangs.  This strategy remains selector-bound:
        coordinates come from the one current matching node and the written
        value is re-verified in a fresh hierarchy.
        """
        if self.serial == "fake":
            self.set_binding_text(selector, value)
            return
        if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
            raise ValueError("adb_shell_input supports printable ASCII input only")
        if "%" in value:
            raise ValueError("adb_shell_input does not support percent characters")
        nodes = binding_nodes(ET.fromstring(self.hierarchy()), selector)
        if len(nodes) != 1:
            raise RuntimeError(f"dynamic selector matched {len(nodes)} nodes")
        match = re.fullmatch(
            r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", nodes[0].get("bounds", ""),
        )
        if match is None:
            raise RuntimeError("adb_shell_input binding has no usable bounds")
        left, top, right, bottom = map(int, match.groups())
        adb = "adb"
        commands = [
            [adb, "-s", self.serial, "shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2)],
            [adb, "-s", self.serial, "shell", "input", "keyevent", "KEYCODE_MOVE_END"],
            [adb, "-s", self.serial, "shell", "input", "keyevent", *(["KEYCODE_DEL"] * 128)],
        ]
        for command in commands:
            completed = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30, check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("adb_shell_input command failed")
        if value:
            completed = subprocess.run(
                [adb, "-s", self.serial, "shell", "input", "text", value.replace(" ", "%s")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30, check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("adb_shell_input text command failed")
        time.sleep(0.3)
        if self.frozen_hierarchy is not None:
            return
        written = binding_nodes(ET.fromstring(self.hierarchy()), selector)
        if len(written) != 1:
            raise RuntimeError(f"binding post-write check matched {len(written)} nodes")
        if written[0].get("password") != "true":
            observed = written[0].get("text", "")
            hint = written[0].get("hint", "")
            if not binding_value_matches(value, observed, hint):
                raise AppInputRejected(
                    "binding post-write value mismatch: "
                    f"expected={value!r}, observed={observed!r}, hint={hint!r}"
                )

    def set_binding_material_date_picker(
        self, selector: dict[str, Any], value: str,
    ) -> None:
        """Enter a date through Android MaterialDatePicker text-input mode."""
        parsed = None
        for date_format in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(value, date_format)
                break
            except ValueError:
                continue
        if parsed is None:
            raise AppInputRejected("Material date value must be YYYY-MM-DD or MM/DD/YYYY")
        self.binding_object(selector).click()
        time.sleep(0.3)
        package = selector["package"]
        toggle = {
            "package": package,
            "resource-id": f"{package}:id/mtrl_picker_header_toggle",
        }
        self.action_object(toggle).click()
        time.sleep(0.3)
        picker_input = {
            "package": package,
            "resource-id": f"{package}:id/mtrl_picker_edit_text_date",
            "class": "android.widget.EditText",
        }
        self.set_binding_text(picker_input, parsed.strftime("%m/%d/%Y"))
        confirm = {
            "package": package,
            "resource-id": f"{package}:id/confirm_button",
        }
        self.action_object(confirm).click()
        time.sleep(0.5)
        nodes = binding_nodes(ET.fromstring(self.hierarchy()), selector)
        if len(nodes) != 1 or not logical_binding_value(
            nodes[0].get("text", ""), nodes[0].get("hint", ""),
        ):
            raise AppInputRejected("Material date picker did not commit a visible date")

    def assertion(self, assertion: dict[str, Any]) -> bool:
        return assertion_in_hierarchy(self.hierarchy(), assertion)

    def binding_assertion(self, assertion: dict[str, Any]) -> bool:
        """Evaluate input assertions one binding at a time across virtualized forms."""
        mode = assertion.get("mode")
        targets = assertion.get("targets")
        if mode not in {"all", "any"} or not isinstance(targets, list) or not targets:
            raise ValueError("invalid binding assertion")
        checks = []
        for target in targets:
            selector = target.get("selector")
            expected = target.get("exists")
            if not isinstance(selector, dict) or not isinstance(expected, bool):
                raise ValueError("invalid binding assertion target")
            try:
                self.binding_object(selector)
                root = ET.fromstring(self.hierarchy())
                nodes = binding_nodes(root, selector)
            except RuntimeError as exc:
                if str(exc) != "dynamic selector matched 0 nodes":
                    raise
                nodes = []
            check = bool(nodes) is expected
            if "attribute" in target or "equals" in target:
                attribute = target.get("attribute")
                if attribute == "text_equals_hint":
                    if target.get("equals") is not True:
                        raise ValueError("text_equals_hint assertions require boolean true")
                    check = (
                        check and len(nodes) == 1
                        and bool(nodes[0].get("hint", ""))
                        and nodes[0].get("text", "") == nodes[0].get("hint", "")
                    )
                elif attribute == "text_non_empty":
                    if target.get("equals") is not True:
                        raise ValueError("text_non_empty assertions require boolean true")
                    check = (
                        check and len(nodes) == 1
                        and bool(logical_binding_value(
                            nodes[0].get("text", ""), nodes[0].get("hint", ""),
                        ))
                    )
                elif attribute not in {"text", "hint", "content-desc"} or not isinstance(target.get("equals"), str):
                    raise ValueError("attribute assertions require a supported attribute and string equals")
                else:
                    observed = nodes[0].get(attribute, "") if len(nodes) == 1 else ""
                    if attribute == "text" and len(nodes) == 1:
                        observed = logical_binding_value(observed, nodes[0].get("hint", ""))
                    check = check and len(nodes) == 1 and observed == target["equals"]
            checks.append(check)
        return all(checks) if mode == "all" else any(checks)

    def candidate_bindings_match(
        self, bindings: dict[str, Any], candidate: dict[str, str],
    ) -> bool:
        """Verify that every live binding still represents its candidate."""
        if set(candidate) != set(bindings):
            raise ValueError("candidate fields must exactly match Oracle bindings")
        root = ET.fromstring(self.hierarchy())
        for resource_id, selector in bindings.items():
            nodes = binding_nodes(root, selector)
            if len(nodes) != 1:
                return False
            if nodes[0].get("password") == "true":
                return False
            if not binding_value_matches(
                candidate[resource_id],
                nodes[0].get("text", ""),
                nodes[0].get("hint", ""),
            ):
                return False
        return True

    def candidate_texts_visible(
        self, package: str, candidate: dict[str, str],
    ) -> bool:
        """Require every non-empty candidate value as exact App-owned UI text."""
        if not isinstance(package, str) or not package or not candidate:
            raise ValueError("candidate text goal requires an App package and candidate")
        if any(not isinstance(value, str) for value in candidate.values()):
            raise ValueError("candidate text goal requires string candidate values")
        if any(not value for value in candidate.values()):
            return False
        root = ET.fromstring(self.hierarchy())
        visible = [
            node.get("text", "")
            for node in root.iter("node")
            if node.get("package") == package
        ]
        return all(
            any(observed == value or observed == accessibility_rendered_value(value)
                for observed in visible)
            for value in candidate.values()
        )

    def fill(
        self,
        bindings: dict[str, Any],
        candidate: dict[str, str],
        input_strategies: dict[str, str] | None = None,
    ) -> None:
        if set(candidate) != set(bindings) or any(not isinstance(value, str) for value in candidate.values()):
            raise ValueError("candidate fields must exactly match Oracle bindings and contain strings")
        input_strategies = input_strategies or {}
        if not isinstance(input_strategies, dict) or not set(input_strategies).issubset(bindings):
            raise ValueError("input strategies must reference frozen bindings")
        if any(
            value not in {
                "adb_key_events", "adb_shell_input", "masked_phone_suffix",
                "material_date_picker",
            }
            for value in input_strategies.values()
        ):
            raise ValueError("unsupported input strategy")
        for resource_id in bindings:
            try:
                if input_strategies.get(resource_id) == "adb_key_events":
                    self.set_binding_text_adb_key_events(
                        bindings[resource_id], candidate[resource_id],
                    )
                elif input_strategies.get(resource_id) == "adb_shell_input":
                    self.set_binding_text_adb_shell(
                        bindings[resource_id], candidate[resource_id],
                    )
                elif input_strategies.get(resource_id) == "masked_phone_suffix":
                    self.set_binding_text(
                        bindings[resource_id], candidate[resource_id],
                        allow_masked_phone_suffix=True,
                    )
                elif input_strategies.get(resource_id) == "material_date_picker":
                    self.set_binding_material_date_picker(
                        bindings[resource_id], candidate[resource_id],
                    )
                else:
                    self.set_binding_text(bindings[resource_id], candidate[resource_id])
            except AppInputRejected as exc:
                raise AppInputRejected(f"{resource_id}: {exc}") from exc
            except Exception as exc:
                raise RuntimeError(f"{resource_id}: {exc}") from exc

    def actions(
        self, actions: list[dict[str, Any]], *, stop_is_error: bool = False,
        observed_hierarchy: str | None = None,
    ) -> bool:
        """Execute a frozen action sequence.

        A later action may be marked ``if_missing=stop`` when its target only
        exists after an earlier validation step succeeds. Its absence is then
        an observed App rejection, not an adapter failure. Calibration uses
        ``stop_is_error=True`` for the positive arm so every frozen step must
        still be demonstrated by a known-good input.
        """
        if not isinstance(actions, list) or not actions:
            raise ValueError("verify_action must be non-empty")
        self.last_action_stop = None
        for action in actions:
            kind = action.get("action")
            try:
                if kind in {
                    "click", "click_if_present", "click_unless_view_roots_exceed",
                    "click_from_observed_hierarchy_unless_view_roots_exceed",
                }:
                    if kind == "click_if_present":
                        selector = action.get("selector")
                        if not isinstance(selector, dict):
                            raise ValueError("click_if_present requires a selector")
                        target = self._maybe_object(selector, binding=False)
                        if target is None:
                            continue
                        target.click()
                    else:
                        if kind in {
                            "click_unless_view_roots_exceed",
                            "click_from_observed_hierarchy_unless_view_roots_exceed",
                        }:
                            package = action.get("package")
                            maximum = action.get("max_view_roots")
                            if (
                                not isinstance(package, str) or not package
                                or type(maximum) is not int or maximum < 1
                            ):
                                raise ValueError(
                                    f"{kind} requires package "
                                    "and positive max_view_roots"
                                )
                            if self.current_app().get("package") != package:
                                raise RuntimeError(
                                    "view-root click guard foreground package does not match"
                                )
                            view_root_count = self.app_view_root_count(package)
                            if view_root_count > maximum:
                                if stop_is_error:
                                    raise RuntimeError(
                                        "positive control stopped before click at App "
                                        "view-root pressure"
                                    )
                                self.last_action_stop = {
                                    "reason": "view_root_pressure_before_click",
                                    "package": package,
                                    "max_view_roots": maximum,
                                    "view_root_count": view_root_count,
                                }
                                return False
                        if kind == "click_from_observed_hierarchy_unless_view_roots_exceed":
                            if not isinstance(observed_hierarchy, str) or not observed_hierarchy:
                                raise ValueError(
                                    "observed-hierarchy click requires a captured hierarchy"
                                )
                            selector = action.get("selector")
                            if not isinstance(selector, dict):
                                raise ValueError(
                                    "observed-hierarchy click requires a selector"
                                )
                            nodes = binding_nodes(
                                ET.fromstring(observed_hierarchy), selector,
                            )
                            if len(nodes) != 1:
                                raise RuntimeError(
                                    f"dynamic selector matched {len(nodes)} nodes"
                                )
                            _CurrentNodeBindingObject(self.device, nodes[0]).click()
                        else:
                            self.action_object(action["selector"]).click()
                elif kind == "double_click":
                    self.action_object(action["selector"]).click()
                    time.sleep(0.1)
                    self.action_object(action["selector"]).click()
                elif kind == "long_click":
                    target = self.action_object(action["selector"])
                    if not hasattr(target, "long_click"):
                        raise RuntimeError("dynamic action target does not support long_click")
                    target.long_click()
                elif kind == "set_text":
                    selector = action.get("selector")
                    value = action.get("value")
                    if not isinstance(selector, dict) or not isinstance(value, str):
                        raise ValueError("set_text requires a selector and string value")
                    # This is intentionally the same verified binding write used
                    # for candidate fields.  It lets begin-attempt prepare a
                    # frozen, reversible invalid UI baseline without editing the
                    # VM snapshot or submitting any remote mutation.
                    self.set_binding_text(selector, value)
                elif kind == "press":
                    self.device.press(action["key"])
                elif kind == "press_if_present":
                    selector = action.get("selector")
                    key = action.get("key")
                    if not isinstance(selector, dict) or not isinstance(key, str) or not key:
                        raise ValueError("press_if_present requires a selector and key")
                    if self._maybe_object(selector, binding=False) is None:
                        continue
                    self.device.press(key)
                elif kind == "stop_if_present":
                    selector = action.get("selector")
                    if not isinstance(selector, dict):
                        raise ValueError("stop_if_present requires a selector")
                    if self._maybe_object(selector, binding=False) is None:
                        continue
                    if stop_is_error:
                        raise RuntimeError(
                            "positive control stopped at an observed terminal App state"
                        )
                    self.last_action_stop = {
                        "reason": "terminal_selector_present",
                        "selector": dict(selector),
                    }
                    return False
                elif kind == "stop_if_view_roots_exceed":
                    package = action.get("package")
                    maximum = action.get("max_view_roots")
                    timeout_seconds = action.get("timeout_seconds")
                    poll_seconds = action.get("poll_seconds")
                    if (
                        not isinstance(package, str) or not package
                        or type(maximum) is not int or maximum < 1
                        or not isinstance(timeout_seconds, (int, float))
                        or not 0 < float(timeout_seconds) <= 30
                        or not isinstance(poll_seconds, (int, float))
                        or not 0 < float(poll_seconds) <= 1
                    ):
                        raise ValueError(
                            "stop_if_view_roots_exceed requires package, "
                            "positive max_view_roots, timeout_seconds in (0,30], "
                            "and poll_seconds in (0,1]"
                        )
                    if self.current_app().get("package") != package:
                        raise RuntimeError("view-root stop guard foreground package does not match")
                    deadline = time.monotonic() + float(timeout_seconds)
                    while True:
                        view_root_count = self.app_view_root_count(package)
                        if view_root_count > maximum:
                            if stop_is_error:
                                raise RuntimeError(
                                    "positive control stopped at App view-root pressure"
                                )
                            self.last_action_stop = {
                                "reason": "view_root_pressure",
                                "package": package,
                                "max_view_roots": maximum,
                                "view_root_count": view_root_count,
                            }
                            return False
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(float(poll_seconds))
                elif kind == "press_back_if_view_roots_exceed":
                    package = action.get("package")
                    maximum = action.get("max_view_roots")
                    timeout_seconds = action.get("timeout_seconds")
                    poll_seconds = action.get("poll_seconds")
                    if (
                        not isinstance(package, str) or not package
                        or type(maximum) is not int or maximum < 1
                        or not isinstance(timeout_seconds, (int, float))
                        or not 0 < float(timeout_seconds) <= 30
                        or not isinstance(poll_seconds, (int, float))
                        or not 0 < float(poll_seconds) <= 1
                    ):
                        raise ValueError(
                            "press_back_if_view_roots_exceed requires package, "
                            "positive max_view_roots, timeout_seconds in (0,30], "
                            "and poll_seconds in (0,1]"
                        )
                    if self.current_app().get("package") != package:
                        raise RuntimeError("view-root guard foreground package does not match")
                    deadline = time.monotonic() + float(timeout_seconds)
                    pressure_observed = False
                    back_presses = 0
                    while True:
                        if self.app_view_root_count(package) > maximum:
                            pressure_observed = True
                            if time.monotonic() >= deadline:
                                raise RuntimeError(
                                    "App view-root pressure did not return to a bounded state"
                                )
                            if back_presses >= 2:
                                raise RuntimeError(
                                    "App view-root pressure survived two bounded Back presses"
                                )
                            self.device.press("back")
                            back_presses += 1
                            # The first Back may only hide the IME; a second is
                            # allowed for the delayed login sheet.  A full
                            # second between them prevents the same sheet-close
                            # animation from consuming both presses and never
                            # permits the guard to navigate beyond two levels.
                            time.sleep(max(1.0, float(poll_seconds)))
                            continue
                        if pressure_observed:
                            break
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(float(poll_seconds))
                elif kind in {"tap", "tap_if_present"}:
                    if kind == "tap_if_present":
                        guard_selector = action.get("guard_selector")
                        if not isinstance(guard_selector, dict):
                            raise ValueError("tap_if_present requires a guard_selector")
                        if self._maybe_object(guard_selector, binding=False) is None:
                            continue
                    package = action.get("package")
                    width = action.get("screen_width")
                    height = action.get("screen_height")
                    x = action.get("x")
                    y = action.get("y")
                    if (
                        not isinstance(package, str) or not package
                        or type(width) is not int or type(height) is not int
                        or type(x) is not int or type(y) is not int
                    ):
                        raise ValueError(
                            "tap requires package and integer x/y/screen dimensions"
                        )
                    current = self.current_app()
                    actual_width, actual_height = self.window_size()
                    if current.get("package") != package:
                        raise RuntimeError("tap foreground package does not match")
                    if (actual_width, actual_height) != (width, height):
                        raise RuntimeError("tap screen dimensions do not match")
                    if not (0 <= x < width and 0 <= y < height):
                        raise ValueError("tap coordinate is outside the frozen screen")
                    if self.serial == "fake":
                        self.device.click(x, y)
                    else:
                        completed = subprocess.run(
                            [
                                "adb", "-s", self.serial, "shell", "input", "tap",
                                str(x), str(y),
                            ],
                            text=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            timeout=15,
                            check=False,
                        )
                        if completed.returncode != 0:
                            raise RuntimeError("guarded adb tap failed")
                elif kind == "swipe":
                    package = action.get("package")
                    width = action.get("screen_width")
                    height = action.get("screen_height")
                    points = [
                        action.get("start_x"), action.get("start_y"),
                        action.get("end_x"), action.get("end_y"),
                    ]
                    duration = action.get("duration_seconds")
                    if (
                        not isinstance(package, str) or not package
                        or type(width) is not int or type(height) is not int
                        or any(type(value) is not int for value in points)
                        or not isinstance(duration, (int, float))
                        or not 0 < float(duration) <= 5
                    ):
                        raise ValueError(
                            "swipe requires package, integer coordinates/screen dimensions, "
                            "and duration_seconds in (0,5]"
                        )
                    current = self.current_app()
                    actual_width, actual_height = self.window_size()
                    if current.get("package") != package:
                        raise RuntimeError("swipe foreground package does not match")
                    if (actual_width, actual_height) != (width, height):
                        raise RuntimeError("swipe screen dimensions do not match")
                    if not all(
                        0 <= x < width and 0 <= y < height
                        for x, y in ((points[0], points[1]), (points[2], points[3]))
                    ):
                        raise ValueError("swipe coordinate is outside the frozen screen")
                    self.device.swipe(*points, float(duration))
                elif kind == "wait":
                    seconds = float(action["seconds"])
                    if seconds <= 0 or seconds > 30:
                        raise ValueError("wait must be in (0,30] seconds")
                    time.sleep(seconds)
                else:
                    raise ValueError(f"unsupported dynamic action: {kind}")
            except Exception as exc:
                missing = (
                    isinstance(exc, RuntimeError)
                    and str(exc) == "dynamic selector matched 0 nodes"
                ) or type(exc).__name__ == "UiObjectNotFoundError"
                if action.get("if_missing") == "stop" and missing:
                    if stop_is_error:
                        raise RuntimeError(
                            "positive control stopped at a conditionally available action"
                        ) from exc
                    self.last_action_stop = {
                        "reason": "conditional_target_missing",
                        "selector": dict(action.get("selector") or {}),
                    }
                    return False
                raise
        return True


def _device_serial(case: dict[str, Any], oracle: dict[str, Any]) -> str:
    serial = oracle.get("device_serial") or case.get("mirror", {}).get("device", {}).get("serial")
    if not isinstance(serial, str) or not serial:
        raise ValueError("device serial is not frozen")
    return serial


def _save_evidence(driver: AndroidDriver, evidence_dir: Path, stem: str) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    state_hash, hierarchy, foreground = driver.state()
    xml_name = f"{stem}.xml"
    xml_path = evidence_dir / xml_name
    xml_path.write_text(hierarchy, encoding="utf-8")
    evidence = [{"path": xml_name, "sha256": sha256_bytes(xml_path.read_bytes()), "kind": "xml"}]
    return state_hash, evidence, foreground


def _ui_diagnostics(
    hierarchy: str,
    targets: list[dict[str, Any]],
    request_id: str,
    evidence_name: str,
) -> list[dict[str, Any]]:
    root = ET.fromstring(hierarchy)
    diagnostics = []
    for target in targets:
        selector = target["selector"]
        attribute = target.get("attribute", "text")
        nodes = [
            node for node in root.iter("node")
            if all(node.get(key) == value for key, value in selector.items())
        ]
        if len(nodes) != 1:
            continue
        detail = nodes[0].get(attribute)
        if not detail:
            continue
        diagnostics.append({
            "type": "observed_ui_message",
            "detail": detail,
            "public": True,
            "source": "ui_observation",
            "checker_id": target["id"],
            "evidence_ref": f"request:{request_id}:{evidence_name}",
        })
    return diagnostics


def begin_attempt(payload: dict[str, Any], *, device: Any | None = None) -> dict[str, Any]:
    case = payload["case"]
    attempt = payload["attempt"]
    oracle = attempt["oracle"]
    contract = oracle["begin_attempt"]
    command_receipt = _run_contract_command(
        contract["reset_command"], payload, float(attempt.get("command_timeout_seconds", 600))
    )
    if command_receipt.get("reset_verified") is not True:
        raise RuntimeError("baseline reset command did not verify reset")
    if command_receipt.get("reset_id") != contract["reset_id"]:
        raise RuntimeError("baseline reset_id drift")
    command_remote = command_receipt.get("remote_baseline")
    if command_remote is None and command_receipt.get("remote_baseline_verified") is True:
        command_remote = "verified"
    if command_remote != contract["remote_baseline"]:
        raise RuntimeError("remote baseline receipt drift")
    reset_command_device_time = command_receipt.get("device_time")
    if reset_command_device_time is not None and not (
        isinstance(reset_command_device_time, dict)
        and reset_command_device_time.get("verified") is True
    ):
        raise RuntimeError("baseline reset did not provide a verified device-time receipt")
    # Some case-specific reset helpers perform their own final VM snapshot
    # load after a wrapper has already synchronized the clock.  Re-synchronize
    # at the adapter boundary so the receipt always describes the device state
    # that will actually be observed and exercised by this attempt.
    from .structured_snapshot_reset import synchronize_device_time

    device_time = synchronize_device_time(_device_serial(case, oracle))
    if device_time.get("verified") is not True:
        raise RuntimeError("post-reset device-time synchronization was not verified")
    hierarchy_transport = oracle.get("execute_turn", {}).get("hierarchy_transport")
    frozen_hierarchy = None
    if hierarchy_transport == "frozen_initial_xml":
        frozen_path = Path(oracle["source_evidence"]["initial_xml"])
        frozen_hierarchy = frozen_path.read_text(encoding="utf-8")
    driver = AndroidDriver(
        _device_serial(case, oracle),
        device=device,
        prefer_adb_hierarchy=(hierarchy_transport == "adb"),
        frozen_hierarchy=frozen_hierarchy,
    )
    target_page_verified = driver.assertion(contract["initial_assertion"])
    target_activity = command_receipt.get("target_activity_start")
    activity_reforegrounded = False
    if (
        not target_page_verified
        and isinstance(target_activity, dict)
        and target_activity.get("verified") is True
        and isinstance(target_activity.get("component"), str)
        and target_activity["component"]
    ):
        # A case-specific reset wrapper may qualify an explicit target Activity
        # after restoring the frozen snapshot. The Activity can still drop to
        # Launcher while the adapter performs its mandatory post-reset clock
        # synchronization. Reapply only that already-reviewed component and
        # require the unchanged frozen page assertion before producing a
        # formal begin receipt.
        completed = subprocess.run(
            [
                "adb", "-s", _device_serial(case, oracle), "shell", "am", "start",
                "-W", "-n", target_activity["component"],
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        combined = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode == 0 and "Status: ok" in combined:
            for _ in range(20):
                time.sleep(0.5)
                if driver.assertion(contract["initial_assertion"]):
                    target_page_verified = True
                    activity_reforegrounded = True
                    break
    state_hash, evidence, foreground = _save_evidence(
        driver, Path(attempt["evidence_dir"]), "attempt_begin"
    )
    if not target_page_verified:
        raise RuntimeError("target page assertion failed after baseline reset")
    receipt = {
        "reset_verified": True,
        "target_page_verified": True,
        "remote_baseline": contract["remote_baseline"],
        "reset_id": contract["reset_id"],
        "state_hash": state_hash,
        "evidence": evidence,
        "foreground": foreground,
    }
    receipt["device_time"] = device_time
    if reset_command_device_time is not None:
        receipt["reset_command_device_time"] = reset_command_device_time
    if command_receipt.get("device_time") is not None:
        receipt["snapshot_load_strategy"] = command_receipt.get("snapshot_load_strategy")
        receipt["network_isolation"] = command_receipt.get("network_isolation")
        receipt["avd_before_reset"] = command_receipt.get("avd_before_reset")
    if activity_reforegrounded:
        receipt["post_clock_target_activity_repair"] = True
        receipt["avd_switch_required"] = command_receipt.get("avd_switch_required")
    return receipt


def execute_turn(payload: dict[str, Any], *, device: Any | None = None) -> dict[str, Any]:
    candidate = payload["candidate"]
    request = payload["request"]
    oracle = request["oracle"]
    evidence_dir = Path(request["evidence_dir"])
    driver = AndroidDriver(_device_serial(request["case"], oracle), device=device)
    before_hash, before_evidence, _ = _save_evidence(driver, evidence_dir, "before")
    try:
        driver.fill(
            oracle["binding"], candidate,
            oracle["execute_turn"].get("input_strategies"),
        )
        actions_completed = driver.actions(oracle["execute_turn"]["verify_action"])
    except AppInputRejected as exc:
        after_hash, after_evidence, foreground = _save_evidence(
            driver, evidence_dir, "input_rejected",
        )
        evidence = before_evidence + after_evidence
        if oracle["execute_turn"].get("input_rejection_verdict") != "fail":
            return {
                "verdict": "infrastructure_error",
                "observation_complete": False,
                "state_before_hash": before_hash,
                "state_after_hash": after_hash,
                "evidence": evidence,
                "evidence_hash": sha256_bytes(canonical_json(evidence).encode("utf-8")),
                "foreground": foreground,
                "diagnostics": [],
                "error_type": type(exc).__name__,
            }
        return {
            "verdict": "fail",
            "observation_complete": True,
            "state_before_hash": before_hash,
            "state_after_hash": after_hash,
            "evidence": evidence,
            "evidence_hash": sha256_bytes(canonical_json(evidence).encode("utf-8")),
            "foreground": foreground,
            "diagnostics": [{
                "id": "app_input_rejected",
                "detail": "The App-owned input widget refused the candidate value.",
            }],
        }
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        after_hash, after_evidence, foreground = _save_evidence(driver, evidence_dir, "action_error")
        evidence = before_evidence + after_evidence
        return {
            "verdict": "infrastructure_error",
            "observation_complete": False,
            "state_before_hash": before_hash,
            "state_after_hash": after_hash,
            "evidence": evidence,
            "evidence_hash": sha256_bytes(canonical_json(evidence).encode("utf-8")),
            "foreground": foreground,
            "diagnostics": [],
            "error_type": type(exc).__name__,
        }
    if actions_completed is False:
        # ``if_missing=stop`` is a calibrated App rejection: an earlier
        # submit/validation step completed, but the conditionally available
        # next target never appeared.  Do not keep polling the goal after that
        # terminal observation.  On unstable Compose surfaces (P094), every
        # extra hierarchy poll can retain another window root until the native
        # hierarchy fallback is killed.  Capture one bounded terminal state
        # and let the normal recovery contract restore the input page.
        after_hash, after_evidence, foreground = _save_evidence(
            driver, evidence_dir, "action_stopped",
        )
        evidence = before_evidence + after_evidence
        hierarchy = (evidence_dir / "action_stopped.xml").read_text(encoding="utf-8")
        diagnostics = _ui_diagnostics(
            hierarchy,
            oracle.get("diagnostic_targets", []),
            request["request_id"],
            "action_stopped.xml",
        )
        diagnostics.append({
            "id": "conditional_action_target_missing",
            "detail": "The App did not expose the conditionally available action target.",
        })
        return {
            "verdict": "fail",
            "observation_complete": True,
            "state_before_hash": before_hash,
            "state_after_hash": after_hash,
            "evidence": evidence,
            "evidence_hash": sha256_bytes(canonical_json(evidence).encode("utf-8")),
            "foreground": foreground,
            "diagnostics": diagnostics,
        }
    deadline = time.monotonic() + float(oracle["execute_turn"]["observation_seconds"])
    passed = False
    candidate_binding_goal = oracle["execute_turn"].get("candidate_binding_goal", False)
    if not isinstance(candidate_binding_goal, bool):
        raise ValueError("candidate_binding_goal must be boolean")
    candidate_text_goal = oracle["execute_turn"].get("candidate_text_goal", False)
    if not isinstance(candidate_text_goal, bool):
        raise ValueError("candidate_text_goal must be boolean")
    while True:
        passed = driver.assertion(oracle["goal_assertion"]) and (
            not candidate_binding_goal
            or driver.candidate_bindings_match(oracle["binding"], candidate)
        ) and (
            not candidate_text_goal
            or driver.candidate_texts_visible(oracle["aut"], candidate)
        )
        if passed or time.monotonic() >= deadline:
            break
        time.sleep(float(oracle["execute_turn"]["poll_seconds"]))
    after_hash, after_evidence, foreground = _save_evidence(driver, evidence_dir, "after")
    evidence = before_evidence + after_evidence
    hierarchy = (evidence_dir / "after.xml").read_text(encoding="utf-8")
    diagnostics = _ui_diagnostics(
        hierarchy,
        oracle.get("diagnostic_targets", []),
        request["request_id"],
        "after.xml",
    )
    observer_command = oracle["execute_turn"].get("observer_command")
    if observer_command:
        observed = _run_contract_command(
            observer_command,
            {**payload, "captured_evidence": evidence, "ui_goal_assertion": passed},
            float(request.get("command_timeout_seconds", 600)),
        )
        verdict = observed.get("verdict")
        complete = observed.get("observation_complete")
        if verdict not in {"pass", "fail", "unknown", "infrastructure_error"} or not isinstance(complete, bool):
            raise RuntimeError("observer command returned an invalid verdict")
        diagnostics.extend(observed.get("diagnostics", []))
    else:
        verdict = "pass" if passed else "fail"
        complete = True
    if verdict in {"pass", "fail"} and complete is not True:
        raise RuntimeError("semantic observer verdict is incomplete")
    if verdict in {"unknown", "infrastructure_error"}:
        complete = False
    return {
        "verdict": verdict,
        "observation_complete": complete,
        "state_before_hash": before_hash,
        "state_after_hash": after_hash,
        "evidence": evidence,
        "evidence_hash": sha256_bytes(canonical_json(evidence).encode("utf-8")),
        "foreground": foreground,
        "diagnostics": diagnostics,
    }


def recover_turn(payload: dict[str, Any], *, device: Any | None = None) -> dict[str, Any]:
    execution = payload["execution"]
    oracle = execution["oracle"]
    contract = oracle["recover_turn"]
    driver = AndroidDriver(_device_serial(execution["case"], oracle), device=device)
    state_before_hash, _, _ = driver.state()
    if state_before_hash != execution.get("state_after_hash"):
        raise RuntimeError("App state drifted between failed execution and recovery")
    receipt = _run_contract_command(
        contract["command"], payload, float(execution.get("command_timeout_seconds", 600))
    )
    forbidden = ("snapshot_loaded", "remote_reset", "renavigated_from_baseline")
    if any(receipt.get(key) is not False for key in forbidden):
        raise RuntimeError("recovery command reset or reloaded the baseline")
    if receipt.get("natural_state_preserved") is not True:
        raise RuntimeError("recovery command did not preserve natural App state")
    state_hash, evidence, foreground = _save_evidence(
        driver, Path(execution["evidence_dir"]), "recovery"
    )
    hierarchy = (Path(execution["evidence_dir"]) / "recovery.xml").read_text(
        encoding="utf-8"
    )
    target_ok = assertion_in_hierarchy(hierarchy, contract["target_page_assertion"])
    input_ok = assertion_in_hierarchy(hierarchy, contract["input_clear_assertion"])
    if not target_ok or not input_ok:
        raise RuntimeError("recovery live assertions failed")
    locator_refresh_hash = sha256_bytes(canonical_json(oracle["binding"]).encode("utf-8"))
    return {
        "snapshot_loaded": False,
        "remote_reset": False,
        "renavigated_from_baseline": False,
        "natural_state_preserved": True,
        "state_before_hash": state_before_hash,
        "target_page_verified": True,
        "input_cleared": True,
        "state_hash": state_hash,
        "locator_refresh_hash": locator_refresh_hash,
        "evidence": evidence,
        "foreground": foreground,
    }


def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    operation = payload.get("operation")
    if operation == "begin_attempt":
        return begin_attempt(payload)
    if operation == "execute_turn":
        return execute_turn(payload)
    if operation == "recover_turn":
        return recover_turn(payload)
    raise ValueError(f"unknown operation: {operation}")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        result = dispatch(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({"error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
