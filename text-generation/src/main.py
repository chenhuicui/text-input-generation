# main.py
"""主程序入口模块，负责协调应用安装、启动、上下文提取及提示生成全流程"""
import sys
import time
from typing import Any, Dict, Tuple

from src.apk_management.installer import PackageInstaller
from src.apk_management.launcher import AppLauncher
from src.context_extraction.context_extractor import ContextExtractor
from src.llm_integration.llm_chatter import LLMChatter
from src.llm_integration.prompt_generator import PromptEngine
from src.llm_integration.text_input_extractor import TextInputExtractor
from src.test_execution.action_executor import ActionExecutor
from src.utils.assert_utils import AssertUtils
from src.utils.db_utils import DBUtils
from src.utils.logger import get_logger, LoggerUtils
from src.utils.uiautomator_utils import UIAutomatorUtils
from src.utils.yaml_utils import YamlUtils

logger = get_logger(__name__)


def main_process(config: Dict[str, Any]) -> None:
    """自动化测试主流程控制器"""

    try:
        # 初始化阶段
        llm_config = YamlUtils.load_llm_config()

        for try_time in range(3):
            logger.info(f"\n第{try_time + 1}次实验 {'=*' * 50}")
            # 应用启动阶段
            launcher, app_config = _launch_and_navigate(config)

            # 上下文处理阶段
            context_data = _extract_context(launcher, app_config)
            prompt = _build_prompt(context_data)

            # LLM交互阶段
            test_text = _process_llm_interaction(llm_config, context_data, prompt)

            # 执行验证阶段
            val = _execute_validation(launcher, app_config, test_text)

            DBUtils.save_result_value(
                app_config['package_name'],
                llm_config['model_type'],
                try_time + 1,
                val,
                0,
                test_text
            )
            UIAutomatorUtils.app_stop(launcher.device, app_config['package_name'])
            UIAutomatorUtils.app_stop(launcher.device, "android")

            if app_config['package_name'] in (
                    "com.applabstudios.ai.mail.homescreen.inbox",
            ):
                time.sleep(20)

    except Exception as e:
        logger.critical(f"主流程异常终止: {e}", exc_info=True)
        raise

    finally:
        logger.info("流程执行完成".center(50))
        logger.info(f"{'=*' * 50}")


def _launch_and_navigate(config: dict) -> Tuple[AppLauncher, dict]:
    """处理应用启动与导航"""
    installer = PackageInstaller()
    success, package_name, message = installer.install_app(config['sources'])

    launcher = AppLauncher()

    if not launcher.launch_app(package_name):
        logger.error("应用启动失败")
        raise RuntimeError("应用启动异常")

    # 如果为首次安装，安装完成后自动化结束进程
    if success == 1:
        logger.info(f"{package_name}安装成功")
        sys.exit(1)

    # 如果已经安装了，就执行脚本
    app_config = YamlUtils.load_app_config(package_name)

    # 动态等待元素，否则等待20秒
    AssertUtils.check_multiple_targets(
        device=launcher.device,
        targets=app_config['delay_detect'],
        by="text",
        is_appear=True,
        timeout=20,
        interval=0.5
    )

    logger.info("=*" * 50)
    if not launcher.navigate_to_target_page(app_config['navigation_steps']):
        logger.error("页面导航失败")
        raise RuntimeError("导航流程异常")

    # 页面稳定等待
    time.sleep(2)
    logger.info("🎉 成功进入目标页面")

    return launcher, app_config


def _extract_context(launcher: AppLauncher, app_config: dict) -> dict:
    """提取运行时上下文"""
    logger.info(f"{'=*' * 50}")
    logger.info(f"🌠 开始提取上下文: {app_config['package_name']}")
    extractor = ContextExtractor(launcher.device)
    return extractor.extract_all_contexts(
        app_name=app_config['app_name'],
        package_name=app_config['package_name']
    )


def _build_prompt(context_data: dict) -> str:
    """构建LLM提示"""
    logger.info(f"{'=*' * 50}")
    logger.info("📒 构建提示工程")
    return PromptEngine().build_prompt(context_data)


def _process_llm_interaction(llm_config: dict, context_data: dict, prompt: str) -> dict:
    """处理LLM交互流程"""

    chatter = LLMChatter(llm_config)
    extractor = TextInputExtractor(
        llm_chatter=chatter,
        max_retries=llm_config['max_retries'],
        context_data=context_data,
    )
    logger.info(f"🤖 开始向{llm_config['model_type']}发送上下文信息 (正在进行第 1/{llm_config['max_retries']} 次尝试)")

    response = chatter.chat_completion(prompt)

    tag, test_text = extractor.extract_test_input(response, prompt)

    if tag == "TAG：次数用完，未成功提取测试用例":
        return {}

    return test_text


def _execute_validation(launcher: AppLauncher, app_config: dict, test_text: dict) -> int:
    """执行验证操作"""
    if not test_text:
        return 0

    action_executor = ActionExecutor(launcher.device)
    action_executor.fill_text_inputs(test_text)
    logger.info("✍️ 测试文本回填完成")

    action_executor.execute_actions(app_config['verify_action'])

    logger.info("=*" * 50)
    logger.info("🤔 执行断言验证")

    verify_result = AssertUtils.verify_oracle(
        launcher.device,
        app_config
    )

    logger.info(f"\t验证结果: {'✅ 通过' if verify_result['all_passed'] else '❌ 未通过'}")
    return 1 if verify_result['all_passed'] else 0


def main():
    """主程序入口"""
    try:
        # 配置加载
        config = YamlUtils.load_config()
        LoggerUtils.setup_logger(config)
        main_process(config)

    except Exception as e:
        logger.critical("主流程异常终止", exc_info=True)
        raise


if __name__ == "__main__":
    main()
