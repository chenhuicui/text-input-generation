import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Tuple, Set, List, Optional, Dict, Union

from src.utils.logger import get_logger
from src.utils.yaml_utils import YamlUtils

logger = get_logger(__name__)


class PackageInstaller:
    """APK/XAPK安装器核心类"""

    def __init__(self, device_id: str = None):
        self.device_id = device_id
        self.installed_packages = None
        self.aapt_path = None
        self.adb_path = None
        self.max_workers = None

    def _build_adb_cmd(self, base_cmd: list) -> list:
        """构造带设备ID的ADB命令"""
        if self.device_id:
            return [self.adb_path, "-s", self.device_id] + base_cmd
        return [self.adb_path] + base_cmd

    def _check_device_connection(self):
        """详细设备连接检查"""
        check_cmd = self._build_adb_cmd(["get-state"])
        try:
            result = subprocess.run(
                check_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,  # 添加超时限制
                check=True
            )

            # 解析设备状态
            state = result.stdout.strip().lower()
            if state != "device":
                status_map = {
                    "offline": "设备已连接但未响应",
                    "unauthorized": "未授权USB调试",
                    "unknown": "未知连接状态",
                    "": "设备未连接"
                }
                raise RuntimeError(f"设备 {self.device_id or '默认'} 状态异常: {status_map.get(state, state)}")

        except subprocess.TimeoutExpired:
            raise RuntimeError("设备连接超时")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"连接检查失败: {e.stderr.strip()}")

    def initialize(self, max_retries=3):
        """带重试机制的初始化"""
        for attempt in range(1, max_retries + 1):
            try:
                self.adb_path = YamlUtils.load_config().get('adb_path')
                self.aapt_path = YamlUtils.load_config().get('aapt_path')
                self.max_workers = YamlUtils.load_config().get('max_workers')

                self._check_environment()

                if self.device_id:
                    logger.debug(f"验证设备连接 (尝试 {attempt}/{max_retries})")
                    self._check_device_connection()

                self.installed_packages = self._get_installed_packages()
                return  # 初始化成功

            except RuntimeError as e:
                if attempt == max_retries:
                    logger.error(f"初始化失败 ({max_retries}次尝试)")
                    raise

                logger.warning(f"初始化失败: {str(e)}，10秒后重试...")
                time.sleep(10)
                self._restart_adb_server()  # 新增ADB服务重启

    def _restart_adb_server(self):
        """重启ADB服务"""
        try:
            subprocess.run(
                [self.adb_path, "kill-server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            subprocess.run(
                [self.adb_path, "start-server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(5)  # 等待服务启动
        except Exception as e:
            logger.error("ADB服务重启失败: {str(e)}")

    def _check_environment(self):
        """检查必要工具是否可用"""
        # 检查adb
        if not Path(self.adb_path).exists():
            raise FileNotFoundError(f"ADB路径不存在: {self.adb_path}")

        # 检查aapt
        if not Path(self.aapt_path).exists():
            raise FileNotFoundError(f"AAPT路径不存在: {self.aapt_path}")

        # 检查adb连接
        try:
            subprocess.run(
                [self.adb_path, "devices"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ADB连接检查失败: {e.stderr.decode()}")

    @staticmethod
    def _format_results(results: list, app_files: list) -> List[dict]:
        """格式化安装结果"""
        return [
            {
                "file": str(file),
                "success": success,
                "package": package,
                "message": message
            }
            for file, (success, package, message) in zip(app_files, results)
        ]

    def install_app(self, apk: str) -> Tuple[int, str, str]:
        """返回 (安装状态, 包名, 错误信息)"""

        file_path = self.get_app_path(apk)
        self.initialize()

        try:
            if file_path.suffix.lower() == '.xapk':
                success, package, message = self._install_xapk(file_path)
            else:
                success, package, message = self._install_apk(file_path)

            if success == -1:
                logger.error(f"{package}安装失败: {message}")
                sys.exit(-1)

            return success, package, message
        except Exception as e:
            logger.error(f"安装失败: {file_path.name}", exc_info=True)
            sys.exit(-1)

    def _install_xapk(self, xapk_path: Path) -> Tuple[int, str, str]:
        """返回 (状态, 包名, 信息)"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                apk_files = self._extract_xapk(xapk_path, tmp_dir)
                package_name = self._validate_package_names(apk_files)

                logger.info(f"📱 尝试拉起app (包名为{package_name})")

                if package_name in self.installed_packages:
                    logger.info(f"\t跳过安装")
                    return 0, package_name, "skipped"

                install_cmd = self._build_adb_cmd(["install-multiple", "-r"])
                install_cmd.extend(str(apk) for apk in apk_files)

                result = subprocess.run(
                    install_cmd,
                    capture_output=True,
                    text=True,
                    check=True
                )

                if 'Success' not in result.stdout:
                    logger.error(f"\t安装失败")
                    return -1, package_name, "failed：" + result.stdout.strip()

                return 1, package_name, "success"

            except subprocess.CalledProcessError as e:
                error = e.stderr or e.stdout
                return -1, package_name, f"ADB错误: {error.strip()}"
            except RuntimeError as e:
                return -1, "", str(e)

    def _extract_xapk(self, xapk_path: Path, tmp_dir: str) -> List[Path]:
        """解压XAPK文件并返回APK列表"""
        with zipfile.ZipFile(xapk_path, 'r') as zip_ref:
            zip_ref.extractall(tmp_dir)

        apk_files = list(Path(tmp_dir).rglob("*.apk"))
        if not apk_files:
            raise RuntimeError("未找到APK文件")
        return apk_files

    def _validate_package_names(self, apk_files: List[Path]) -> str:
        """验证并返回唯一包名"""
        package_names = set()
        for apk in apk_files:
            try:
                name = self._parse_package_name(apk)
                package_names.add(name)
            except RuntimeError:
                continue

        if not package_names:
            raise RuntimeError("所有APK文件均未找到有效包名")
        if len(package_names) > 1:
            raise RuntimeError(f"发现多个不同包名: {', '.join(package_names)}")
        return package_names.pop()

    def _install_apk(self, apk_path: Path) -> Tuple[int, str, str]:
        """返回 (状态, 包名, 信息)"""
        try:
            package_name = self._parse_package_name(apk_path)
            logger.info(f"📱 尝试拉起app (包名为{package_name})")

            if package_name in self.installed_packages:
                logger.info(f"\t跳过安装{package_name}")
                return 0, package_name, "skipped"
            install_cmd = self._build_adb_cmd(["install", "-r", str(apk_path)])
            result = subprocess.run(
                install_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )

            if 'Success' not in result.stdout:
                logger.error(f"{package_name}安装失败")
                return -1, package_name, f"failed：{result.stdout.strip()}"
            return 1, package_name, "success"

        except subprocess.CalledProcessError as e:
            error = e.stderr or e.stdout
            return -1, "", f"ADB错误: {error.strip()}"
        except Exception as e:
            return -1, "", str(e)

    def _parse_package_name(self, apk_path: Path) -> str:
        """解析APK包名"""
        try:
            cmd = [self.aapt_path, "dump", "badging", str(apk_path)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            for line in result.stdout.splitlines():
                if line.startswith("package: name="):
                    package_name = line.split("'")[1]
                    return package_name
            raise RuntimeError("未找到包名信息")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"aapt执行失败: {e.stderr.strip()}")

    def _get_installed_packages(self) -> Set[str]:
        """安全获取已安装包列表"""
        cmd = self._build_adb_cmd(["shell", "pm", "list", "packages"])

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,  # 增加超时时间
                check=True
            )

            # 检查实际输出内容
            if "error:" in result.stderr.lower():
                raise RuntimeError(f"ADB命令执行错误: {result.stderr.strip()}")

            return set(
                line.split(":")[1].strip()
                for line in result.stdout.splitlines()
                if line.startswith("package:")
            )

        except subprocess.CalledProcessError as e:
            error_msg = f"获取安装列表失败: {e.stderr.strip() or e.stdout.strip()}"
            if "no devices/emulators found" in error_msg.lower():
                error_msg += "\n可能原因：1.设备未连接 2.未启用USB调试"
            logger.error(error_msg)
            raise
        except subprocess.TimeoutExpired:
            logger.error("获取安装包列表超时，请检查设备响应")
            raise

    def get_app_path(self, source: str) -> Path:
        """收集待安装应用文件"""
        path = Path(source)
        if not (path.exists() and path.suffix.lower() in ('.xapk', '.apk')):
            logger.error(f"{source}路径错误")
            sys.exit(-1)
        return path

    def _print_summary(self, results: list):
        """修改后的摘要打印"""
        success = sum(1 for s, _, _ in results if s == 1)
        skipped = sum(1 for s, _, _ in results if s == 0)
        failed = sum(1 for s, _, _ in results if s == -1)
        logger.info(
            f"安装完成: {success} 成功 | {skipped} 跳过 | {failed} 失败 | 总计 {len(results)}"
        )

    InstallResult = Dict[str, Union[bool, str]]

    @staticmethod
    def validate_install_results(results: List[InstallResult]) -> Optional[str]:
        """验证安装结果并返回首个成功安装的包名"""
        success_results = [r for r in results if r.get('success') in [0, 1]]

        if not success_results:
            logger.error("所有APK安装均失败")
            return None

        first_success = success_results[0]
        logger.info(f"成功安装应用: {first_success.get('package', '')}")
        return first_success.get('package', '')
