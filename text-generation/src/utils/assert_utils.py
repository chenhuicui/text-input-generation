import logging
import sys
import time
from typing import List, Dict, Union

from uiautomator2 import Device

from src.utils.uiautomator_utils import UIAutomatorUtils

logger = logging.getLogger(__name__)


class AssertUtils:

    @staticmethod
    def verify_oracle(device: Device, oracle_config: Dict) -> Dict:

        result = {
            "all_passed": True,
            "details": []
        }

        # 检查是否存在至少一个验证项
        has_appear = 'verify_appear' in oracle_config
        has_disappear = 'verify_disappear' in oracle_config

        if not (has_appear or has_disappear):
            result['all_passed'] = False
            result['details'].append({
                "type": "config_error",
                "message": "Oracle配置错误：必须至少包含verify_appear或verify_disappear中的一个"
            })
            return result

        # 处理verify_disappear验证
        if has_disappear:
            disappear_config = oracle_config['verify_disappear']
            disappear_detail = AssertUtils.check_multiple_targets(
                device=device,
                targets=disappear_config.get('targets', []),
                by=disappear_config.get('by', []),
                is_appear=False,
                timeout=disappear_config.get('timeout', 20),
                interval=0.5,
                is_all_passed=True if disappear_config.get('mode', 'all') == 'all' else False
            )
            if disappear_detail:
                result['details'].append(disappear_detail)
                result['all_passed'] = result['all_passed'] and disappear_detail['passed']

        logger.info(f"{'=' * 50}")

        # 处理verify_appear验证
        if has_appear:
            appear_config = oracle_config['verify_appear']
            logger.info(True if appear_config.get('mode', 'all') == 'all' else False)
            appear_detail = AssertUtils.check_multiple_targets(
                device=device,
                targets=appear_config.get('targets', []),
                by=appear_config.get('by', []),
                is_appear=True,
                timeout=appear_config.get('timeout', 20),
                interval=0.5,
                is_all_passed=True if appear_config.get('mode', 'all') == 'all' else False
            )
            if appear_detail:
                result['details'].append(appear_detail)
                result['all_passed'] = result['all_passed'] and appear_detail['passed']

        return result

    @staticmethod
    def check_multiple_targets(
            device: Device,
            targets: List[str],
            by: str,
            is_appear: bool,
            timeout: int = 20,
            interval: float = 0.5,
            is_all_passed: bool = True
    ) -> Dict:
        """多目标循环验证（动态倒计时+状态变迁记录）"""
        start_time = time.time()
        overall_passed = False
        results = []
        status_history = {}  # 记录每个目标的状态变迁历史
        console_width = 80

        # 初始化数据结构
        for target in targets:
            results.append({
                "target": target,
                "by": by,
                "passed": False,
                "status": "未检查",
                "history": ["未检查"]
            })
            status_history[target] = ["未检查"]

        logger.info(
            f"\t▶️ 开始验证页面元素：{len(targets)} 个目标 | 预期{'出现' if is_appear else '消失'} ｜ 超时设置：{timeout}s")

        try:
            sys.stdout.write("\n")  # 预输出空行

            while True:
                elapsed = time.time() - start_time
                remaining = max(timeout - elapsed, 0)

                # 动态倒计时显示
                sys.stdout.write(f"\r⏳ 剩余时间: {remaining:5.1f}秒 | 正在持续监测...")

                current_all_passed = is_all_passed  # 初始化逻辑状态

                for idx, target in enumerate(targets):
                    actual_exists = False
                    try:
                        elem = UIAutomatorUtils.find_element(device, by, target)
                        actual_exists = elem.exists
                        current_status = "组件出现" if actual_exists else "组件消失"
                    except Exception as e:
                        current_status = "检查异常"
                        sys.stdout.write("\033[s\033[F\033[2K\033[u")
                        logger.error(f"❌ [{target}] 检查异常: {str(e)}")
                        results[idx]["error"] = str(e)
                        if is_all_passed:
                            current_all_passed = False  # 严格模式直接失败

                    # 状态变迁处理
                    history = status_history[target]
                    simplified_history = history.copy()
                    if current_status != simplified_history[-1]:
                        sys.stdout.write("\r" + " " * console_width + "\r")
                        simplified_history.append(current_status)
                        status_history[target] = simplified_history

                    # 更新目标状态
                    passed = (bool(actual_exists) == bool(is_appear))
                    results[idx].update({
                        "passed": passed,
                        "status": current_status,
                        "history": simplified_history.copy()
                    })

                    # 聚合验证结果
                    if is_all_passed:
                        current_all_passed &= bool(passed)
                    else:
                        current_all_passed |= bool(passed)
                        if current_all_passed:
                            break  # 任意满足模式快速退出

                # 退出条件判断
                if current_all_passed or elapsed >= timeout:
                    if current_all_passed:
                        success_type = "全部满足" if is_all_passed else "任一满足"
                        logger.info(f"\t\t✅ 验证通过! 耗时{elapsed:.1f}秒 ({success_type})")
                        overall_passed = True
                    else:
                        logger.warning(f"\t\t⛔ 验证超时! 未在{timeout}秒内满足条件")
                    break

                time.sleep(interval)
        finally:
            sys.stdout.write("\n")  # 确保换行

        # 最终状态输出（修复空值问题）
        for res in results:
            if len(res["history"]) == 0:
                res["history"] = ["未检查"]
            final_status = res["history"][-1] if res["history"] else "未知状态"
            logger.debug(f"\t - {res['target']} 最终状态: {final_status}")

        return {
            "type": "appear" if is_appear else "disappear",
            "passed": overall_passed,
            "targets": targets,
            "details": results
        }