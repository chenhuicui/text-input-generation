# src/utils/uiautomator_utils.py
import logging
import time
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Tuple, List, Union

import cv2
from uiautomator2 import Device, connect

from src.utils.str_utils import StrUtils

logger = logging.getLogger(__name__)


class UIAutomatorUtils:
    """UI Automator 操作工具类（静态方法）"""

    @staticmethod
    def connect_device(serial: Optional[str] = None) -> Device:
        """连接设备"""
        try:
            return connect(serial) if serial else connect()
        except Exception as e:
            raise RuntimeError(f"设备连接失败: {str(e)}")

    @staticmethod
    def app_start(device: Device, package: str, activity: Optional[str] = None) -> None:
        """启动应用"""
        device.app_start(package, activity)

    @staticmethod
    def app_stop(device: Device, package: str) -> None:
        """停止应用"""
        device.app_stop(package)

    @staticmethod
    def click_coordinates(device: Device, x: int, y: int, action_type: str) -> None:
        """点击指定坐标"""
        if action_type == "click":
            device.click(x, y)
        elif action_type == "double_click":
            device.double_click(x, y)
        elif action_type == "long_click":
            device.long_click(x, y)

    @staticmethod
    def double_click_coordinates(device: Device, x: int, y: int) -> None:
        """点击指定坐标"""
        device.double_click(x, y)

    @staticmethod
    def long_click_coordinates(device: Device, x: int, y: int) -> None:
        """点击指定坐标"""
        device.long_click(x, y)

    @staticmethod
    def get_current_app(device: Device) -> Dict:
        """获取当前前台应用信息"""
        return device.app_current()

    @staticmethod
    def find_element(device: Device, by: str, value: str, index: str = None):
        """增强型元素定位方法（支持索引限定）

        Args:
            device: uiautomator设备对象
            by: 定位方式 (text/resource-id/xpath)
            value: 定位值
            index: 元素在匹配列表中的索引（从0开始）

        Returns:
            UiObject: 匹配的元素对象

        Raises:
            ValueError: 无效定位方式
            IndexError: 索引超出范围
        """
        # 基础定位逻辑
        # 基础定位逻辑
        if by == "text":
            elements = device(text=value)
        elif by == "resource-id":
            elements = device(resourceId=value)
        elif by == "xpath":
            if index is not None:
                raise NotImplementedError("XPath定位暂不支持索引参数")
            return device.xpath(value)
        else:
            raise ValueError(f"无效定位方式: {by}")

        # 处理索引限定
        if index is not None:
            # 获取元素总数
            element_count = elements.count

            # 索引有效性校验
            if int(index) >= element_count:
                raise IndexError(f"索引越界，共找到 {element_count} 个元素")

            # 通过索引获取具体元素
            return elements[int(index)]  # 直接使用索引访问

        return elements

    @staticmethod
    def take_screenshot(device: Device) -> "cv2.Mat":
        """获取OpenCV格式截图"""
        return device.screenshot(format='opencv')

    @staticmethod
    def fill_text_into_element_by_id(device: Device, element_id: str, text: str):
        index_str = None
        try:
            if StrUtils.SEPARATOR in element_id:
                element_id, index_str = StrUtils.parse_component_id(element_id)
                elem = UIAutomatorUtils.find_element(device, "resource-id", element_id, index_str)
            else:
                elem = UIAutomatorUtils.find_element(device, "resource-id", element_id)
            elem.set_text(text)
            logger.info(
                f"\t✍️ 元素 {element_id} {'' if index_str is None else StrUtils.SEPARATOR + index_str} 被回填 {text} 完成")
            return True

        except Exception as e:
            logger.info(
                f"\t❌ 元素 {element_id} {'' if index_str is None else StrUtils.SEPARATOR + index_str} 被回填 {text}失败")
            return False

    @staticmethod
    def image_match(screenshot: "cv2.Mat", template_path: str, threshold: float) -> Tuple[int, int, int, int]:
        """执行图像匹配"""
        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            raise ValueError(f"无法加载模板图片: {template_path}")

        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            raise RuntimeError(f"未找到匹配图片 (置信度: {max_val:.2f})")

        h, w = template.shape[:2]
        return (
            max_loc[0] + w // 2,  # 中心X坐标
            max_loc[1] + h // 2,  # 中心Y坐标
            w,  # 模板宽度
            h  # 模板高度
        )

    @staticmethod
    def dump_hierarchy(device: Device) -> str:
        """获取当前UI层级XML"""
        return device.dump_hierarchy()

    @staticmethod
    def parse_xml_root(xml_content: str) -> ET.Element:
        """解析XML字符串为ElementTree根节点"""
        return ET.fromstring(xml_content)

    @staticmethod
    def get_current_app_info(device: Device) -> Dict:
        """获取当前前台应用信息"""
        return device.app_current()

    @staticmethod
    def get_device_info(device: Device) -> Dict:
        """获取设备基础信息"""
        return device.info

    @staticmethod
    def find_nodes(root: ET.Element, xpath: str) -> List[ET.Element]:
        """通过XPath查找节点"""
        return root.findall(xpath)

    @staticmethod
    def get_node_attribute(node: ET.Element, attr: str, default: str = "") -> str:
        """安全获取节点属性值"""
        return node.attrib.get(attr, default)

    @staticmethod
    def click_element(device: Device, target: str, by: str = "text") -> bool:
        """执行点击操作"""
        element = UIAutomatorUtils.find_element(device, by, target)
        if element.exists:
            element.click()
            return True
        return False

    @staticmethod
    def perform_step(device: Device, step_config: Dict) -> bool:
        action_type = step_config["action"]
        if action_type in ["click", "double_click", "long_click"]:
            return UIAutomatorUtils.perform_click(device, step_config, action_type)
        elif action_type in "swipe":
            return UIAutomatorUtils.perform_swipe(device, step_config)

    @staticmethod
    def perform_click(device: Device, step_config: Dict, action_type: str) -> bool:
        """执行单个导航步骤"""
        time.sleep(step_config.get("delay", 2.0))
        step_type = step_config.get("type", "text")

        try:
            if step_type == "coordinate":
                return UIAutomatorUtils._handle_coordinate_step(device, step_config, action_type)
            elif step_type == "enter":
                return UIAutomatorUtils._handle_enter_step(device, step_config)
            elif step_type == "back":
                return UIAutomatorUtils._handle_back_step(device, step_config)
            return UIAutomatorUtils._handle_element_step(device, step_config)
        except Exception as e:
            logger.error(f"步骤执行失败: {step_config}", exc_info=True)
            return False

    @staticmethod
    def perform_swipe(device: Device, step_config: Dict) -> bool:
        """执行单个导航步骤"""
        time.sleep(step_config.get("delay", 2.0))
        raw_fx_hex = step_config["raw_fx_hex"]
        raw_fy_hex = step_config["raw_fy_hex"]
        raw_tx_hex = step_config["raw_tx_hex"]
        raw_ty_hex = step_config["raw_ty_hex"]
        try:
            screen_width, screen_height = UIAutomatorUtils._get_screen_resolution(device)
            fx, fy = UIAutomatorUtils._convert_touch_coordinates(raw_fx_hex, raw_fy_hex, screen_width, screen_height)
            tx, ty = UIAutomatorUtils._convert_touch_coordinates(raw_tx_hex, raw_ty_hex, screen_width, screen_height)
            return device.swipe(fx, fy, tx, ty)
        except Exception as e:
            logger.error(f"步骤执行失败: {step_config}", exc_info=True)
            return False

    @staticmethod
    def swipeFromTo(device: Device, raw_fx_hex: str, raw_fy_hex: str, raw_tx_hex: str, raw_ty_hex: str) -> bool:
        """执行单个导航步骤"""
        try:
            screen_width, screen_height = UIAutomatorUtils._get_screen_resolution(device)
            fx, fy = UIAutomatorUtils._convert_touch_coordinates(raw_fx_hex, raw_fy_hex, screen_width, screen_height)
            tx, ty = UIAutomatorUtils._convert_touch_coordinates(raw_tx_hex, raw_ty_hex, screen_width, screen_height)
            return device.swipe(fx, fy, tx, ty)
        except Exception as e:
            logger.error(f"滑动步骤执行失败", exc_info=True)
            return False

    @staticmethod
    def _handle_enter_step(device, step_config):
        device.press("enter")
        time.sleep(step_config.get("delay", 2.0))
        return True

    @staticmethod
    def _handle_back_step(device, step_config):
        device.press("back")
        time.sleep(step_config.get("delay", 2.0))
        return True

    @staticmethod
    def _handle_element_step(device: Device, config: Dict) -> bool:
        """处理元素操作步骤"""
        action = config["action"]
        target = config["target"]
        by = config.get("by", "text")
        retry = config.get("retry", 3)


        for attempt in range(1, retry + 1):
            element = UIAutomatorUtils.find_element(device, by, target)
            if element.exists:
                element_info = f"{by.capitalize()}: {target}"
                getattr(element, action)()
                logger.info(f"\t👋 操作成功 | 元素: {element_info} | 尝试次数: {attempt}")
                return True

            logger.debug(f"元素未找到 | 尝试 {attempt}/{retry} | 类型: {by} | 目标: {target}")
            time.sleep(1)

        logger.error(f"✘ 操作失败 | 未找到元素 | 类型: {by} | 目标: {target}")
        return False

    @staticmethod
    def _handle_coordinate_step(device: Device, config: Dict, action_type: str) -> bool:
        try:
            raw_x_hex = config["raw_x_hex"]
            raw_y_hex = config["raw_y_hex"]
            screen_width, screen_height = UIAutomatorUtils._get_screen_resolution(device)
            x, y = UIAutomatorUtils._convert_touch_coordinates(raw_x_hex, raw_y_hex, screen_width, screen_height)
            UIAutomatorUtils.click_coordinates(device, x, y, action_type)
            return True
        except RuntimeError as e:
            logger.error("坐标点击失败")
            return False

    @staticmethod
    def _convert_touch_coordinates(
            raw_x_hex: str,
            raw_y_hex: str,
            screen_width: int = 1280,
            screen_height: int = 2856,
            device_max_x: int = 0x7FFF,  # 默认安卓设备坐标范围
            device_max_y: int = 0x7FFF
    ) -> tuple:
        """
        将触摸事件原始坐标转换为屏幕像素坐标

        :param raw_x_hex: 十六进制X坐标值 (例如 '74ff')
        :param raw_y_hex: 十六进制Y坐标值 (例如 'b3f')
        :param screen_width: 屏幕宽度像素
        :param screen_height: 屏幕高度像素
        :param device_max_x: 设备X轴最大原始值
        :param device_max_y: 设备Y轴最大原始值
        :return: (x_pixel, y_pixel)
        """
        # 转换十六进制到十进制
        raw_x = int(raw_x_hex, 16)
        raw_y = int(raw_y_hex, 16)

        # 坐标映射计算
        x_pixel = int((raw_x / device_max_x) * screen_width)
        y_pixel = int((raw_y / device_max_y) * screen_height)

        # 确保坐标在屏幕范围内
        x_pixel = max(0, min(x_pixel, screen_width - 1))
        y_pixel = max(0, min(y_pixel, screen_height - 1))

        return x_pixel, y_pixel

    @staticmethod
    def _get_screen_resolution(device: Device = None) -> tuple:
        """通过 uiautomator 自动获取屏幕分辨率"""
        try:
            device_info = UIAutomatorUtils.get_device_info(device)
            screen_width = device_info["displayWidth"]
            screen_height = device_info["displayHeight"]

            return screen_width, screen_height

        except KeyError:
            raise RuntimeError("无法获取屏幕分辨率，请检查设备连接")
        except Exception as e:
            raise RuntimeError(f"设备连接失败: {str(e)}")

    @staticmethod
    def click_back(device):
        device.press("back")

    @staticmethod
    def type_character_by_character(device, element, text, delay=0.1):
        """
        逐个字符输入文本到指定的UI元素

        :param device: uiautomator2 设备实例
        :param element: 要输入的UI元素
        :param text: 要输入的文本
        :param delay: 每个字符输入后的延迟(秒)
        """
        # 确保元素存在且可见
        if not element.exists:
            raise Exception("元素不存在")

        # 点击元素获取焦点
        element.click()

        # 清空现有文本（可选）
        element.clear_text()

        # 逐个字符输入
        for char in text:
            # 使用设备输入方法输入单个字符
            device.send_keys(char)
            time.sleep(delay)


if __name__ == "__main__":
    device_1 = connect()
    # UIAutomatorUtils.handle_coordinate_step()
    screenshot_1 = UIAutomatorUtils.take_screenshot(device_1)
    x1, y1, w1, h1 = UIAutomatorUtils.image_match(screenshot_1,
                                                  '/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study/configs/apk_config/tar_imgs/img_1.png',
                                                  0.2)
    print(x1, y1)
    # 在截图上绘制标记（红色矩形框 + 中心十字线）
    # 1. 绘制矩形框（基于左上角坐标和宽高）
    top_left = (x1 - w1 // 2, y1 - h1 // 2)  # 根据中心坐标反推左上角坐标
    bottom_right = (x1 + w1 // 2, y1 + h1 // 2)
    cv2.rectangle(screenshot_1, top_left, bottom_right, (0, 0, 255), 2)  # 红色边框，线宽2

    # 2. 绘制中心十字线（绿色）
    cross_size = 20
    cv2.line(screenshot_1, (x1 - cross_size, y1), (x1 + cross_size, y1), (0, 255, 0), 2)  # 横线
    cv2.line(screenshot_1, (x1, y1 - cross_size), (x1, y1 + cross_size), (0, 255, 0), 2)  # 竖线

    # 保存或显示结果
    cv2.imwrite("marked_screenshot.png", screenshot_1)  # 保存标记后的图片
    cv2.imshow("Result", screenshot_1)  # 显示图片窗口
    cv2.waitKey(0)  # 等待按键关闭窗口
    cv2.destroyAllWindows()
    # device_1.click(1175, 252)
