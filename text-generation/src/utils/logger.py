import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional

COLORS = {
    "TIMESTAMP": "\033[34m",  # 时间戳 - 蓝色
    "LOGGER": "\033[35m",  # 类名 - 紫色
    "LEVEL": {
        "DEBUG": "\033[36m",  # 青色
        "INFO": "\033[32m",  # 绿色
        "WARNING": "\033[33m",  # 黄色
        "ERROR": "\033[31m",  # 红色
        "CRITICAL": "\033[31;1m"  # 亮红
    },
    "RESET": "\033[0m"
}


class ColoredFormatter(logging.Formatter):
    """分字段彩色日志格式化器"""

    def format(self, record):
        # 构建彩色格式模板
        colored_format = (
            f"{COLORS['TIMESTAMP']}%(asctime)s{COLORS['RESET']} - "
            f"{COLORS['LEVEL'].get(record.levelname, '')}%(levelname)s{COLORS['RESET']} - "
            "%(message)s "
            f"({COLORS['LOGGER']}%(filename)s:%(lineno)d{COLORS['RESET']})"
        )

        # 保存原始格式并临时修改
        original_style = self._style
        self._style = logging.PercentStyle(colored_format)

        # 生成格式化结果
        result = super().format(record)

        # 恢复原始格式
        self._style = original_style
        return result


# 预定义日志格式
LOG_FORMATS: Dict[str, str] = {
    "verbose": "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)",
    "simple": "%(asctime)s - %(levelname)s - %(message)s"
}


class ConsoleFilter(logging.Filter):
    """控制台日志过滤器"""

    def filter(self, record):
        debug_blacklist = [
            'urllib3.connectionpool',
            'uiautomator2.core',
            'uiautomator2'
        ]
        if record.levelno == logging.DEBUG and record.name in debug_blacklist:
            return False
        return True


def setup_logging(
        log_dir: str = "logs",
        log_file: str = "execution.log",
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        level: str = "INFO",
        format_name: str = "verbose"
) -> None:
    """初始化日志系统"""
    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 配置基础设置
    log_level = getattr(logging, level.upper(), logging.INFO)

    # 创建格式器
    console_formatter = ColoredFormatter(LOG_FORMATS[format_name])
    file_formatter = logging.Formatter(LOG_FORMATS[format_name])

    # 主日志器配置
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # 清理现有处理器
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # ================= 控制台处理器 =================
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(ConsoleFilter())
    logger.addHandler(console_handler)

    # ================= 文件处理器 =================
    file_handler = RotatingFileHandler(
        filename=log_path / log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # 异常处理钩子
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取日志记录器"""
    return logging.getLogger(name)

class LoggerUtils:
    @staticmethod
    def setup_logger(config: Dict):
        setup_logging(
            log_dir=config['log_config']['log_dir'],
            log_file=config['log_config']['log_file'],
            level=config['log_config']['log_level'].upper()
        )
