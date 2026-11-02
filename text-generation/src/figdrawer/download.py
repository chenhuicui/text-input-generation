import mysql.connector
from google_play_scraper import app
import time
import pandas as pd
import re
import os
from datetime import datetime

# 配置MySQL连接
DB_CONFIG = {
    'host': '101.201.82.24',  # 数据库主机地址
    'user': 'must_lab_db_user',  # 数据库用户名
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',  # 数据库密码
    'database': 'must_lab_db'  # 数据库名称
}

# Excel文件配置
EXCEL_FILENAME = "google_play_stats.xlsx"
SHEET_NAME = "App Downloads"


def get_distinct_apps():
    """从MySQL获取distinct app_name列表"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    query = "SELECT DISTINCT app_name FROM t_droidbot_results"
    cursor.execute(query)
    apps = [row[0] for row in cursor.fetchall()]

    cursor.close()
    conn.close()
    return apps


def parse_install_count(installs_str):
    """将Google Play的安装量字符串转换为数字"""
    if not installs_str:
        return 0

    # 移除逗号和加号
    clean_str = installs_str.replace(',', '').replace('+', '').strip()

    # 处理范围表示 (e.g. "1,000-5,000")
    if '-' in clean_str:
        parts = re.split(r'[-–]', clean_str)  # 支持不同种类的短横线
        if len(parts) == 2:
            try:
                low = int(parts[0].strip())
                high = int(parts[1].strip())
                return (low + high) // 2  # 取中间值
            except:
                return 0

    # 处理纯数字
    try:
        return int(clean_str)
    except:
        return 0


def get_play_store_info(app_id):
    """获取Google Play应用信息"""
    try:
        result = app(
            app_id,
            lang='en',  # 语言
            country='us'  # 国家/地区
        )
        return {
            'App ID': app_id,
            'App Name': result.get('title', ''),
            'Installs Text': result.get('installs', ''),
            'Installs Count': parse_install_count(result.get('installs', '')),
            'Rating': result.get('score', 0),
            'Reviews': result.get('reviews', 0),
            'Last Updated': result.get('updated', ''),
            'Version': result.get('version', ''),
            'Developer': result.get('developer', ''),
            'Genre': result.get('genre', ''),
            'Status': 'Success'
        }
    except Exception as e:
        print(f"Error fetching {app_id}: {str(e)}")
        return {
            'App ID': app_id,
            'App Name': '',
            'Installs Text': '',
            'Installs Count': 0,
            'Rating': 0,
            'Reviews': 0,
            'Last Updated': '',
            'Version': '',
            'Developer': '',
            'Genre': '',
            'Status': f"Error: {str(e)}"
        }


def save_to_excel(data, filename, sheet_name):
    """将数据保存到Excel文件"""
    # 创建DataFrame
    df = pd.DataFrame(data)

    # 如果文件存在，添加新工作表而不是覆盖
    if os.path.exists(filename):
        with pd.ExcelWriter(filename, engine='openpyxl', mode='a') as writer:
            # 生成唯一的工作表名称
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_sheet_name = f"{sheet_name}_{timestamp}"
            df.to_excel(writer, sheet_name=unique_sheet_name, index=False)
    else:
        df.to_excel(filename, sheet_name=sheet_name, index=False)

    print(f"\nResults saved to: {filename}")


def main():
    # 获取distinct应用列表
    apps = get_distinct_apps()
    print(f"Found {len(apps)} distinct apps")

    # 存储结果
    results = []
    success_count = 0
    fail_count = 0

    # 遍历每个应用
    for idx, app_id in enumerate(apps):
        print(f"Processing ({idx + 1}/{len(apps)}): {app_id}")

        # 获取Google Play信息
        app_info = get_play_store_info(app_id)
        results.append(app_info)

        if app_info['Status'] == 'Success':
            success_count += 1
        else:
            fail_count += 1

        # 添加延迟避免被封禁 (2-5秒/请求)
        time.sleep(2 + (idx % 3))

    # 保存结果到Excel
    save_to_excel(results, EXCEL_FILENAME, SHEET_NAME)

    # 打印摘要
    print("\nSummary:")
    print(f"Total apps processed: {len(apps)}")
    print(f"Successfully fetched: {success_count}")
    print(f"Failed to fetch: {fail_count}")


if __name__ == "__main__":
    main()