# experiment_main_5554.py
"""主程序入口模块，负责协调应用安装、启动、上下文提取及提示生成全流程"""
import json
import sys
import time
from typing import Dict, Tuple, List

from mysql.connector import pooling

from src.apk_management.installer import PackageInstaller
from src.apk_management.launcher import AppLauncher
from src.test_execution.action_executor import ActionExecutor
from src.utils.assert_utils import AssertUtils
from src.utils.logger import get_logger, LoggerUtils
from src.utils.uiautomator_utils import UIAutomatorUtils
from src.utils.yaml_utils import YamlUtils

logger = get_logger(__name__)


def main_process() -> None:
    """自动化测试主流程控制器"""
    apk_name = "FramMe+Photo+Editor+&+Collage_0.51_apkcombo.com.xapk"

    try:
        # 应用启动阶段
        pkg_name = get_app_id_by_apk_name(apk_name)


        # 从数据库获取测试用例
        records = fetch_google_results(pkg_name)

        for record in records:
            launcher, app_config = _launch_and_navigate(apk_name)
            test_text = json.loads(record['texts'])  # 转换JSON字符串为字典
            model_type = record['model_type']
            seq = record['seq']
            prompt_structure = record['prompt_structure']

            logger.info(f"****************** 当前参数组合：{model_type} | seq = {seq} | prompt = {prompt_structure} ******************")

            # 执行验证阶段
            val = _execute_validation(launcher, app_config, test_text)

            # 保存结果
            save_mock_result_value(
                app_id=pkg_name,
                model_type=model_type,
                seq=seq,
                val=val,
                prompt_structure=prompt_structure,
                texts=test_text
            )

            UIAutomatorUtils.app_stop(launcher.device, pkg_name)
            UIAutomatorUtils.app_stop(launcher.device, "android")

            if pkg_name in ("com.applabstudios.ai.mail.homescreen.inbox",):
                time.sleep(20)

        # 更新manual_check_flag为2
        update_manual_check_flag(pkg_name, 2)

    except Exception as e:
        logger.critical(f"主流程异常终止: {e}", exc_info=True)
        raise

    finally:
        logger.info("流程执行完成".center(50))
        logger.info(f"{'=*' * 50}")


def fetch_google_results(app_id: str) -> List[Dict]:
    """获取指定应用的测试用例"""
    try:
        db_config = YamlUtils.load_db_config()
        connection_pool = pooling.MySQLConnectionPool(
            pool_name=db_config.get('pool_name', 'mypool'),
            pool_size=db_config.get('pool_size', 5),
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
        conn = connection_pool.get_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
                SELECT app_id, model_type, seq, val, prompt_structure, texts 
                FROM t_google_results 
                WHERE app_id = %s and val is null
            """
        cursor.execute(query, (app_id,))
        return cursor.fetchall()
    except Exception as e:
        logger.error(f"数据库查询失败: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


def _launch_and_navigate(apk_name: str) -> Tuple[AppLauncher, dict]:
    """处理应用启动与导航"""
    device_id = "emulator-5554"
    installer = PackageInstaller(device_id)
    success, package_name, message = installer.install_app('/Volumes/Extreme Pro/ttt/apk/' + apk_name)

    launcher = AppLauncher(device_id)

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
        timeout=35,
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
        main_process()

    except Exception as e:
        logger.critical("主流程异常终止", exc_info=True)
        raise


def save_mock_result_value(app_id: str, model_type: str, seq: int, val: int, prompt_structure: int, texts: dict):
    """
    插入或更新记录（根据 appid + model_type + seq 判断是否存在）

    :param app_id: 应用ID
    :param model_type: 模型类型
    :param seq: 序列号
    :param val: 值
    :param prompt_structure: 提示码
    :param texts: JSON文本数据
    """
    try:
        db_config = YamlUtils.load_db_config()

        # 创建连接池
        _connection_pool = pooling.MySQLConnectionPool(
            pool_name=db_config.get('pool_name', 'mypool'),
            pool_size=db_config.get('pool_size', 5),
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
    except Exception as e:
        raise e

    # SQL语句（使用ON DUPLICATE KEY UPDATE实现插入/更新）
    query = """
        INSERT INTO t_google_results
            (app_id, model_type, seq, val, prompt_structure, texts, update_time)
        VALUES 
            (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            val = VALUES(val),
            update_time = NOW()
        """

    # 参数化查询的变量
    params = (
        app_id,
        model_type,
        seq,
        val,
        prompt_structure,
        json.dumps(texts)  # 将字典转为JSON字符串
    )

    # 从连接池获取连接
    try:
        with _connection_pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Database operation failed: {e}")
    finally:
        if 'cursor' in locals():
            cursor.close()


def update_manual_check_flag(app_id: str, flag: int) -> None:
    """更新手动检查标志"""
    try:
        db_config = YamlUtils.load_db_config()
        connection_pool = pooling.MySQLConnectionPool(
            pool_name=db_config.get('pool_name', 'mypool'),
            pool_size=db_config.get('pool_size', 5),
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
        conn = connection_pool.get_connection()
        cursor = conn.cursor()
        query = "UPDATE t_google_apks SET manual_check_flag = %s WHERE app_id = %s"
        cursor.execute(query, (flag, app_id))
        conn.commit()
    except Exception as e:
        logger.error(f"更新manual_check_flag失败: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_app_id_by_apk_name(apk_name: str) -> str:
    """根据apk名称获取应用包名"""
    try:
        db_config = YamlUtils.load_db_config()
        connection_pool = pooling.MySQLConnectionPool(
            pool_name=db_config.get('pool_name', 'mypool'),
            pool_size=db_config.get('pool_size', 5),
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
        conn = connection_pool.get_connection()
        cursor = conn.cursor(dictionary=True)

        # 参数化查询防止SQL注入
        query = "SELECT app_id FROM t_google_apks WHERE apk_name = %s"
        cursor.execute(query, (apk_name,))

        result = cursor.fetchone()

        if not result:
            raise ValueError(f"未找到apk_name为 {apk_name} 的记录")

        return result['app_id']

    except Exception as e:
        logger.error(f"数据库查询异常: {str(e)}")
        raise
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    main()
