# src/context_extraction/context_extractor.py
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import adbutils
import cv2
import uiautomator2
from uiautomator2 import Device

from src.utils.logger import get_logger
from src.utils.str_utils import StrUtils
from src.utils.uiautomator_utils import UIAutomatorUtils

logger = get_logger(__name__)


class ContextExtractor:
    def __init__(self, device: Device):
        self.device = device
        self.hierarchy_xml = None
        self.root = None

    def dump_ui_hierarchy(self, package_name: str) -> str:
        """提取并返回原始XML层次结构"""
        raw_xml = UIAutomatorUtils.dump_hierarchy(self.device)
        root = UIAutomatorUtils.parse_xml_root(raw_xml)
        self._prune_xml_tree(root, package_name)
        self.hierarchy_xml = ET.tostring(root, encoding="utf-8").decode()
        self.root = root
        return self.hierarchy_xml

    def _prune_xml_tree(self, node: ET.Element, target_pkg: str) -> bool:
        """优化版XML树修剪方法"""
        # 标记是否保留当前节点
        keep = False

        # 当前节点属于目标包
        if node.get("package") == target_pkg:
            keep = True

        # 倒序处理子节点避免删除影响索引
        for child in reversed(list(node)):
            if self._prune_xml_tree(child, target_pkg):
                keep = True
            else:
                # 直接移除不需要的子节点
                node.remove(child)

        return keep

    def extract_all_contexts(self, app_name: str, package_name: str) -> Dict:
        """提取并整合所有上下文信息"""

        try:
            # 截图上下文
            screenshot_path = self._save_screenshot(package_name)
            logger.info(f"\t📸 截图保存成功 | 路径: {screenshot_path}")

            # UI层级处理
            xml_content = self.dump_ui_hierarchy(package_name)
            xml_path = self._save_xml_data(xml_content)
            logger.info(f"\t📄 UI层级解析完成 | 路径：{xml_path}")

            # 组件上下文提取
            component_contexts = self.extract_component_contexts()
            logger.info(f"\t✅ 组件上下文就绪（发现 {len(component_contexts)} 个输入组件）")

            # 全局上下文
            global_contexts = self.extract_global_context(app_name, package_name, len(component_contexts))
            logger.info("\t✅ 全局上下文就绪")

            # 相邻上下文分析
            adjacent_contexts = self.extract_adjacent_contexts(component_contexts)
            logger.info("\t✅ 相邻关系分析完成 ")

            # 整合数据
            contexts = {
                "global": global_contexts,
                "component": component_contexts,
                "adjacent": adjacent_contexts
            }

        except Exception as e:
            logger.critical(f"🚨 上下文提取流程异常终止 | 错误: {str(e)}", exc_info=True)
            raise RuntimeError("上下文提取失败") from e

        logger.info("🎉 上下文提取流程完成")
        return contexts

    def extract_global_context(self, app_name: str, package_name: str, text_input_number: int) -> Dict:
        """提取全局上下文"""
        current_app = UIAutomatorUtils.get_current_app_info(self.device)
        return {
            "app_name": app_name,
            "package_name": package_name,
            "activity": current_app.get('activity'),
            "input_count": text_input_number,
        }

    def extract_component_contexts(self) -> List[Dict]:
        """提取可见的输入组件"""
        device_info = UIAutomatorUtils.get_device_info(self.device)
        screen_width = device_info["displayWidth"]
        screen_height = device_info["displayHeight"]

        INPUT_CLASSES = [
            'android.widget.EditText',
            'android.widget.AutoCompleteTextView',
            'android.widget.MultiAutoCompleteTextView'
        ]
        input_nodes = []
        for cls in INPUT_CLASSES:
            input_nodes += UIAutomatorUtils.find_nodes(
                self.root,
                f".//node[@class='{cls}']"
            )
        input_nodes = list({id(node): node for node in input_nodes}.values())

        visible_inputs = []
        for node in input_nodes:

            if UIAutomatorUtils.get_node_attribute(node, "resource-id") == "":
                logger.error(f"❌ app异常，输入框的id字段无法获得，请选择比的页面，或者更换app")
                sys.exit(-1)
            if (UIAutomatorUtils.get_node_attribute(node, "resource-id") in (
                    # 时间选择框排除
                    "com.kajda.fuelio:id/initialDate",
                    "com.omronhealthcare.omronconnect:id/actv_date"
            )):
                logger.warning(f"{UIAutomatorUtils.get_node_attribute(node, 'resource-id')}为时间选择框，跳过")
                continue

            bounds = self._parse_bounds(UIAutomatorUtils.get_node_attribute(node, "bounds", "[0,0][0,0]"))
            if (UIAutomatorUtils.get_node_attribute(node, "package", "") in (
                    # clickable 白名单
                    "com.applabstudios.ai.mail.homescreen.inbox"
            )):
                visible_inputs.append(node)
                continue

            if (self._is_visible(bounds, screen_width, screen_height)
                    and UIAutomatorUtils.get_node_attribute(node, "clickable", "false") == "true"):
                visible_inputs.append(node)

        return self.get_visible_inputs_attributes(visible_inputs)

    def get_visible_inputs_attributes(self, visible_inputs):
        components = []
        for node in visible_inputs:
            components.append({
                "index": UIAutomatorUtils.get_node_attribute(node, "index", "0"),
                "type": UIAutomatorUtils.get_node_attribute(node, "class"),
                "hint": UIAutomatorUtils.get_node_attribute(node, "hint"),
                "text": UIAutomatorUtils.get_node_attribute(node, "text"),
                "resource_id": UIAutomatorUtils.get_node_attribute(node, "resource-id"),
                "bounds": self._parse_bounds(UIAutomatorUtils.get_node_attribute(node, "bounds")),
                "resource_id_combined": ""
            })

        id_counter = {}
        # 第一次遍历统计重复
        for c in components:
            rid = c["resource_id"]
            id_counter[rid] = id_counter.get(rid, 0) + 1

        # 第二次遍历生成ID
        for c in components:
            rid = c["resource_id"]
            if id_counter[rid] > 1:
                # 使用不可见字符拼接
                final_id = f"{rid}{StrUtils.SEPARATOR}{c['index']}"
            else:
                final_id = rid
            c.update({"resource_id_combined": final_id})

        return components

    def extract_adjacent_contexts(self, text_inputs: List[Dict]) -> Dict[str, Dict]:
        adjacent_contexts = {}

        for edit_data in text_inputs:
            # 确保使用统一的键名（如resource-id）

            text_nodes = UIAutomatorUtils.find_nodes(self.root, ".//node[@class='android.widget.TextView']")
            # 获取textinput的边界值以及中心点坐标
            edit_bounds = edit_data["bounds"]
            et_center = self._calculate_center(edit_bounds)

            # 初始化每一个方向上的候选textview列表
            direction_candidates = {"top": [], "bottom": [], "left": [], "right": []}

            # 获取每一个候选与当前edittext的位置关系
            for tv_node in text_nodes:
                tv_bounds = self._parse_bounds(UIAutomatorUtils.get_node_attribute(tv_node, "bounds", "[0,0][0,0]"))
                tv_text = UIAutomatorUtils.get_node_attribute(tv_node, "text", "").strip()

                tv_center = self._calculate_center(tv_bounds)
                direction = self._determine_relative_position(edit_bounds, tv_bounds, tv_center)
                if direction is None:
                    continue
                if direction in ("left", "right"):
                    distance = abs(et_center[0] - tv_center[0])
                else:
                    distance = abs(et_center[1] - tv_center[1])

                direction_candidates[direction].append({
                    "text": tv_text,
                    "distance": distance
                })

            adjacent = {}
            for direction, candidates in direction_candidates.items():
                adjacent[direction] = sorted(candidates, key=lambda x: x["distance"])[0] if candidates else None
            resource_id = edit_data["resource_id_combined"]
            adjacent_contexts[resource_id] = adjacent
            logger.info(f"\t\t{adjacent}")
        return adjacent_contexts

    def _determine_relative_position(
            self,
            edit_bounds: Dict[str, int],
            tv_bounds: Dict[str, int],
            tv_center: Tuple[float, float]
    ) -> Optional[str]:

        if (tv_bounds["bottom"] <= edit_bounds["top"]) and (
                edit_bounds["left"] <= tv_center[0] <= edit_bounds["right"]):
            return "top"

        if (tv_bounds["top"] >= edit_bounds["bottom"]) and (
                edit_bounds["left"] <= tv_center[0] <= edit_bounds["right"]):
            return "bottom"

        if (tv_bounds["right"] <= edit_bounds["left"]) and (
                edit_bounds["top"] <= tv_center[1] <= edit_bounds["bottom"]):
            return "left"

        if (tv_bounds["left"] >= edit_bounds["right"]) and (
                edit_bounds["top"] <= tv_center[1] <= edit_bounds["bottom"]):
            return "right"

        return None

    def _parse_bounds(self, bounds_str: str) -> Dict:
        if not bounds_str or '][' not in bounds_str:
            return {"left": 0, "top": 0, "right": 0, "bottom": 0}

        try:
            cleaned = [s.replace("[", "").replace("]", "") for s in bounds_str.split("][")]
            parts = cleaned[0].split(",") + cleaned[1].split(",")
            left, top, right, bottom = map(int, parts)
            return {"left": left, "top": top, "right": right, "bottom": bottom}
        except Exception as e:
            logger.error(f"解析bounds失败: {bounds_str}, 错误: {e}")
            return {"left": 0, "top": 0, "right": 0, "bottom": 0}

    def _is_visible(self, bounds: Dict, screen_w: int, screen_h: int) -> bool:
        return (0 <= bounds["left"] < screen_w and
                0 <= bounds["top"] < screen_h and
                bounds["right"] <= screen_w and
                bounds["bottom"] <= screen_h)

    def _calculate_center(self, bounds: Dict) -> Tuple[float, float]:
        return (bounds["right"] + bounds["left"]) / 2, (bounds["bottom"] + bounds["top"]) / 2

    def _save_xml_data(self, xml_content: str) -> Path:
        package = UIAutomatorUtils.get_current_app_info(self.device).get('package', 'unknown')
        xml_dir = Path("output/xml_dumps")
        xml_dir.mkdir(parents=True, exist_ok=True)

        file_path = xml_dir / f"hierarchy_{package}.xml"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(xml_content)

        return file_path

    def _save_screenshot(self, package_name: str) -> Path:
        """保存设备截图到文件"""
        max_retries = 5
        retry_delay = 1

        for attempt in range(max_retries):
            try:


                filename = f"screenshot_{package_name}.png"
                screenshot_path = Path("output/screenshots") / filename

                screenshot = self.device.screenshot(format='opencv')
                cv2.imwrite(str(screenshot_path), screenshot)

                return screenshot_path
            except (uiautomator2.HTTPError, adbutils.AdbError) as e:
                if attempt < max_retries - 1:
                    logger.warning(f"\t\t❌📷 截图失败（第 {attempt + 1} 次重试）")
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                logger.error(f"重试机制失效，截图保存失败: {str(e)}")
                raise RuntimeError("无法保存屏幕截图") from e

            except Exception as e:
                logger.error(f"非abd导致截图保存失败: {str(e)}")
                raise RuntimeError("无法保存屏幕截图") from e
