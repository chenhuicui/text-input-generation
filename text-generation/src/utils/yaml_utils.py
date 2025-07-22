"""YAML 配置文件处理工具模块"""

from pathlib import Path
from typing import Any, Dict, Union, List, Optional
import yaml

from src.utils.logger import get_logger

logger = get_logger(__name__)


class YamlUtils:
    """YAML 配置文件处理工具类"""

    @staticmethod
    def load_config() -> Dict[str, Any]:
        """加载并验证YAML配置文件 """
        config_path = Path(__file__).parent.parent.parent / "configs" / "install_config.yaml"
        try:
            with config_path.open('r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

            # 路径规范化处理
            config['aapt_path'] = str(Path(config.get('aapt_path', '')).expanduser().resolve())
            config['adb_path'] = str(Path(config.get('adb_path', '')).expanduser().resolve())

            # 处理APK来源路径
            config['sources'] = config.get('source', [])

            # 验证必要配置项
            required_keys = ['log_config', 'aapt_path', 'adb_path', 'source']
            if missing := [k for k in required_keys if k not in config]:
                raise ValueError(f"缺少必要配置项: {missing}")

            return config

        except FileNotFoundError as e:
            logger.error(f"配置文件不存在: {config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"YAML解析失败: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"配置加载异常: {str(e)}")
            raise

    AppConfig = Dict[str, Union[str, List[Dict]]]

    @staticmethod
    def load_app_config(package_name: str) -> AppConfig:
        """加载应用特定配置（增强型存在性检查）"""
        config_path = Path(__file__).parent.parent.parent / "configs" / "apk_config" / f"{package_name}.yaml"
        default_config = {
            "app_name": "",
            "package_name": "",
            "navigation_steps": [],
            "verify_action": [],
            "delay_detect": []
        }

        def process_verify(config_key: str) -> Optional[dict]:
            """智能处理验证配置项"""
            # 只在配置实际存在时处理
            if config_key not in config:
                return None

            verify_config = config[config_key]
            targets = verify_config.get('targets', [])

            # 空值防御（处理null/空字符串等情况）
            if not targets:
                return None

            # 类型统一化处理
            processed_targets = [targets] if isinstance(targets, str) else targets

            return {
                'targets': processed_targets,
                'by': verify_config.get('by', 'text'),
                'mode': verify_config.get('mode', 'all'),
                'timeout': verify_config.get('timeout', 20),
            }

        try:
            with config_path.open('r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

                # 动态生成有效验证配置
                valid_verifications = {}
                for key in ['verify_appear', 'verify_disappear']:
                    verification = process_verify(key)
                    if verification:
                        valid_verifications[key] = verification

                # 必须至少有一个有效验证
                if not valid_verifications:
                    logger.error(
                        f"配置验证失败：{package_name}.yaml 需要至少一个有效的verify_appear或verify_disappear配置")
                    return default_config

                # 构建最终配置（只保留有效项）
                final_config = {
                    "app_name": config.get("app_name", package_name),
                    "delay_detect": config.get("delay_detect", []),
                    "package_name": config.get("package_name", package_name),
                    "navigation_steps": config.get("navigation_steps", []),
                    "verify_action": config.get("verify_action", []),
                    **valid_verifications  # 动态合并有效验证项
                }

                return final_config

        except FileNotFoundError:
            logger.warning(f"应用配置文件不存在: {package_name}.yaml，使用默认配置")
            return default_config
        except Exception as e:
            logger.error(f"配置加载失败: {package_name}", exc_info=True)
            return default_config

    @staticmethod
    def load_llm_config():
        config_path = Path(__file__).parent.parent.parent / "configs" / "llm_config.yaml"
        with open(config_path) as f:
            llm_config = yaml.safe_load(f)['llm_config']
        return llm_config

    @staticmethod
    def load_db_config():
        config_path = Path(__file__).parent.parent.parent / "configs" / "db_config.yaml"
        with open(config_path) as f:
            db_config = yaml.safe_load(f)['mysql']
        return db_config

    @staticmethod
    def load_prompt_config():
        config_path = Path(__file__).parent.parent.parent / "configs" / "prompt_templates.yaml"
        with open(config_path) as f:
            db_config = yaml.safe_load(f)['prompt_templates']
        return db_config
