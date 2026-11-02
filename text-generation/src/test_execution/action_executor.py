import logging
import time
from typing import Dict, List

from uiautomator2 import Device

from src.utils.uiautomator_utils import UIAutomatorUtils

logger = logging.getLogger(__name__)


class ActionExecutor:
    """回填text与执行ui操作"""

    def __init__(self, device: Device):
        self.device = device

    def fill_text_inputs(self, test_data: Dict[str, str]) -> bool:
        """执行文本回填操作"""
        try:
            if test_data.get('wish-form-url-input'):
                try:
                    UIAutomatorUtils.fill_text_into_element_by_id(self.device, "wish-form-title-input",
                                                                  test_data["wish-form-title-input"])
                    time.sleep(0.2)
                    UIAutomatorUtils.fill_text_into_element_by_id(self.device, "wish-form-price-input",
                                                                  test_data["wish-form-price-input"])
                    time.sleep(0.2)
                    UIAutomatorUtils.swipeFromTo(
                        self.device,
                        raw_fx_hex="00004465",
                        raw_fy_hex="0000568a",
                        raw_tx_hex="00004465",
                        raw_ty_hex="0000307f"
                    )

                    UIAutomatorUtils.fill_text_into_element_by_id(self.device, "wish-form-description-input",
                                                                  test_data["wish-form-description-input"])
                    time.sleep(0.2)
                    UIAutomatorUtils.fill_text_into_element_by_id(self.device, "wish-form-url-input",
                                                                  test_data["wish-form-url-input"])
                    time.sleep(0.2)
                    return True
                except Exception as e:
                    logger.error(f"输入操作失败: {str(e)}")
                    return False
            for element_id, text in test_data.items():
                if not UIAutomatorUtils.fill_text_into_element_by_id(self.device, element_id, text):
                    return False
                time.sleep(0.2)
            return True
        except Exception as e:
            logger.error(f"输入操作失败: {str(e)}")
            return False

    def execute_actions(self, actions: List[Dict]) -> bool:
        try:
            for step in actions:
                if not UIAutomatorUtils.perform_step(self.device, step):
                    return False
            return True
        except Exception as e:
            logger.error("验证UI操作执行失败", exc_info=True)
            return False
