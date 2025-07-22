import json
import os
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from androguard.core.apk import APK
from androguard.core.axml import AXMLPrinter

import pymysql
from pymysql.cursors import DictCursor

DB_CONFIG = {
    'host': '101.201.82.24',
    'user': 'must_lab_db_user',
    'password': 'must_lab_dbQAZ165230kg',
    'database': 'must_lab_db',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


def contains_text_inputs(apk_path: str) -> dict:
    """增强版解析函数 - 支持二进制XML和完整类名匹配"""
    result = {
        "status": "error",
        "components": [],
        "error": None,
        "layout_files": []
    }

    try:
        a = APK(apk_path)
        found = False

        for xml_path in a.get_files():
            # 扩展布局文件路径匹配规则
            if not (('res/layout' in xml_path) and xml_path.endswith(('.xml', '.xml.gz'))):
                continue

            try:
                # 获取二进制数据
                data = a.get_file(xml_path)

                # 处理可能的GZIP压缩（Android 9+特性）
                if xml_path.endswith('.gz'):
                    import gzip
                    data = gzip.decompress(data)

                # 解析二进制XML
                axml = AXMLPrinter(data)
                root = axml.get_xml_obj()

                file_components = []
                for elem in root.iter():
                    # 处理带命名空间的标签（如{http://schemas.android.com/apk/res/android}EditText）
                    tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

                    # 匹配全类名和短类名
                    if any(key in tag for key in ['EditText', 'AutoCompleteTextView', 'MultiAutoCompleteTextView']):
                        found = True
                        component = {
                            "type": tag.split('.')[-1],  # 取最后一段作为类型
                            "full_class": tag,
                            "attributes": {
                                k.split('}')[-1]: v  # 处理带命名空间的属性
                                for k, v in elem.attrib.items()
                            }
                        }
                        file_components.append(component)

                if file_components:
                    result["layout_files"].append({
                        "path": xml_path,
                        "components": file_components
                    })

            except Exception as e:
                result["error"] = f"XML解析错误 ({xml_path}): {str(e)}"
                continue

        result["status"] = "found" if found else "not_found"

        return result

    except Exception as e:
        result["error"] = f"APK处理失败: {str(e)}"
        return result


def process_xapk(xapk_path: str) -> dict:
    """重构后的XAPK处理函数 返回聚合结果"""
    result = {
        "status": "not_found",
        "components": [],
        "error": None,
        "layout_files": []
    }

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(xapk_path, 'r') as z:
                z.extractall(tmpdir)

            # 递归查找所有APK文件
            apk_paths = []
            for root, _, files in os.walk(tmpdir):
                for f in files:
                    if f.lower().endswith('.apk'):
                        full_path = Path(root) / f
                        apk_paths.append(full_path)

            # 处理每个APK并聚合结果
            for apk_path in apk_paths:
                apk_result = contains_text_inputs(str(apk_path))
                if apk_result["status"] == "found":
                    result["status"] = "found"
                    result["components"].extend(apk_result["components"])
                    result["layout_files"].extend(apk_result["layout_files"])
                if apk_result["error"]:
                    result["error"] = apk_result["error"]

    except Exception as e:
        result["error"] = f"XAPK处理失败: {str(e)}"

    return result


def scan_files(apk_paths: list) -> dict:
    """新版文件扫描函数"""
    report = {
        "metadata": {
            "scan_time": datetime.now().isoformat(),
            "scanned_files": 0,
            "found_count": 0,
            "error_count": 0
        },
        "results": []
    }

    for full_path in apk_paths:
        if not os.path.isfile(full_path):
            continue

        filename = os.path.basename(full_path)
        file_result = None

        if filename.lower().endswith('.apk'):
            print(f"[*] 处理 APK: {filename}")
            file_result = contains_text_inputs(full_path)
            file_result.update({
                "filename": filename,
                "type": "APK"
            })

        elif filename.lower().endswith('.xapk'):
            print(f"[*] 处理 XAPK: {filename}")
            file_result = process_xapk(full_path)
            file_result.update({
                "filename": filename,
                "type": "XAPK"
            })

        strsss = filename + "\t" + str(file_result["status"]) + "\n"
        with open('test.txt', 'a+') as writers:  # 打开文件
            writers.write(strsss)

        if file_result:
            report["results"].append(file_result)
            report["metadata"]["scanned_files"] += 1

            if file_result["status"] == "found":
                report["metadata"]["found_count"] += 1
            if file_result["error"]:
                report["metadata"]["error_count"] += 1

    return report


def save_report(report: dict, output_file: str = f"input_scan_report {time.strftime('%Y-%m-%d %H:%M:%S')}.json"):
    """保存扫描报告到 JSON 文件"""
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n报告已保存至: {output_file}")
    except Exception as e:
        print(f"\n保存报告失败: {str(e)}")


def select_apk_name_from_database_by_update_date(date):
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)

        with connection.cursor() as cursor:
            # 使用参数化查询防止SQL注入
            sql = """
                SELECT apk_name
                FROM t_google_apks 
                WHERE update_date IS NOT NULL
                AND update_date > %s
                AND apk_name IS NOT NULL
            """
            # 执行查询（自动提交事务）
            cursor.execute(sql, (date,))

            # 获取全部结果
            result = cursor.fetchall()

            return result

    except pymysql.Error as e:
        print(f"数据库操作失败: {str(e)}")
        # 可根据需要记录日志或抛出异常
        return None

    finally:
        if connection:
            connection.close()


def update_auto_check_flag(apk_name: str, flag: int):
    """更新数据库标记字段"""
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            sql = """
                UPDATE t_google_apks 
                SET auto_check_flag = %s
                WHERE apk_name = %s
            """
            cursor.execute(sql, (flag, apk_name))
        connection.commit()
        print(f"[+] 更新成功：{apk_name} -> {flag}")
    except pymysql.Error as e:
        print(f"[!] 数据库更新失败 {apk_name}: {str(e)}")
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    # 获取需要更新的APK列表
    apk_names = select_apk_name_from_database_by_update_date('2025-03-31')
    print(f"[*] 发现待处理文件：{len(apk_names)}个")

    # 构建完整路径
    base_dir = '/Volumes/Extreme Pro/ttt/apk/'
    apk_paths = [os.path.join(base_dir, an['apk_name']) for an in apk_names]

    # 执行扫描
    report = scan_files(apk_paths)

    # 更新数据库
    # print("\n=== 开始更新数据库 ===")
    # for item in report["results"]:
    #     flag = 1 if item["status"] == "found" else 0
    #
    #     print(item["filename"], flag)
    #     # update_auto_check_flag(item["filename"], flag)
    #
    # # 保存报告
    # save_report(report)
    # print("\n=== 处理完成 ===")
