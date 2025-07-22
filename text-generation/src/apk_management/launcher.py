# src/apk_management/launcher.py
import sys
import time
from typing import Dict, List, Union, Optional

from uiautomator2 import Device

from src.utils.logger import get_logger
from src.utils.uiautomator_utils import UIAutomatorUtils

logger = get_logger(__name__)


class AppLauncher:
    """应用启动与页面导航控制器"""

    def __init__(self, device_serial: Optional[str] = None):
        try:
            self.device: Device = UIAutomatorUtils.connect_device(device_serial)
            self.current_pkg: Optional[str] = None
        except Exception as e:
            logger.error("设备初始化失败", exc_info=True)
            raise

    def launch_app(self,
                   package_name: str,
                   main_activity: Optional[str] = None,
                   stop_before_start: bool = True) -> bool:
        """启动目标应用"""
        try:
            if stop_before_start:
                UIAutomatorUtils.app_stop(self.device, package_name)
                UIAutomatorUtils.app_stop(self.device, "android")

            UIAutomatorUtils.app_start(self.device, package_name, main_activity)




            if not self._wait_until_launched(package_name, timeout=30):
                logger.error(f"应用启动超时: {package_name}")
                return False

            self.current_pkg = package_name
            return True
        except Exception as e:
            logger.error(f"启动应用失败: {package_name}", exc_info=True)
            return False

    def navigate_to_target_page(self, navigation_steps: List[Dict[str, Union[str, float]]]) -> bool:
        """执行导航流程"""
        if not self.current_pkg:
            logger.error("请先启动应用")
            return False

        try:
            if not navigation_steps:
                return True
            for step in navigation_steps:
                if not UIAutomatorUtils.perform_step(self.device, step):
                    return False
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.error("页面导航失败", exc_info=True)
            return False

    def _wait_until_launched(self, package_name: str, timeout: int = 10) -> bool:
        """等待应用启动完成"""
        start_time = time.time()
        last_pkg = None
        check_interval = 1.0  # 检查间隔
        progress_interval = 3  # 进度提示间隔(秒)

        while time.time() - start_time < timeout:
            current = UIAutomatorUtils.get_current_app(self.device)

            current_pkg = current.get("package")
            elapsed = round(time.time() - start_time, 1)
            remaining = timeout - elapsed

            # 包名发生变化时记录
            if current_pkg != last_pkg:
                last_pkg = current_pkg

            # 进度提示（每3秒）
            if int(elapsed) % progress_interval == 0 and int(elapsed) > 0:
                logger.info(f"⏳ 启动等待中 | 已等待 {elapsed}s | 剩余 {remaining}s | 当前应用: {current_pkg or '未知'}")

            if current_pkg == package_name:
                logger.info(f"✅ 应用启动验证成功 | 耗时 {elapsed}s | 当前包名: {current_pkg}")
                return True

            time.sleep(check_interval)
            if package_name in (
                    "com.vkontakte.android",
                    "com.ubercab",
                "com.gametime.gametime"
            ):
                UIAutomatorUtils.click_back(self.device)
        # 超时处理
        final_pkg = UIAutomatorUtils.get_current_app(self.device).get("package")
        logger.warning(
            f"⛔ 启动等待超时 | 总等待 {timeout}s | 最终包名: {final_pkg or '未知'} | 预期包名: {package_name}")
        return False
