import os
import time
from datetime import datetime

import pymysql
from fake_useragent import UserAgent
from google_play_scraper import search
from pymysql.cursors import DictCursor

ua = UserAgent()
headers = {"User-Agent": ua.random}

DB_CONFIG = {
    'host': '101.201.82.24',
    'user': 'must_lab_db_user',
    'password': 'must_lab_dbQAZ165230kg',
    'database': 'must_lab_db',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


def get_apps_from_google_play(app_name):
    print(f"开始处理{app_name}")
    try:
        results = search(app_name, lang='en', country='us')
    except TypeError as e:
        print(e)
        app = {
            "app_name": app_name,
            "app_id": "",
            "score": 0,
            "developer": "",
            "install_num": "",
            "category": "",
        }
        return app

    app = {
        "app_name": app_name,
        "app_id": results[0]['appId'],
        "score": results[0].get("score", 0),
        "developer": results[0]["developer"],
        "install_num": results[0].get("installs", 0),
        "category": results[0]["genre"],
    }
    print(app)
    return app


def save_to_database(app):
    """将应用数据存储到数据库"""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            sql = """
                INSERT INTO t_google_apks 
                    (app_name, app_id, score, developer, install_num, category, infor_crawl_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    app_name = VALUES(app_name),
                    score = VALUES(score),
                    developer = VALUES(developer),
                    install_num = VALUES(install_num),
                    category = VALUES(category),
                    infor_crawl_date = VALUES(infor_crawl_date)
            """

            crawl_time = datetime.now()
            batch_data = [
                (app['app_name'], app['app_id'], app.get('score', 0),
                 app['developer'], app.get('install_num', 'N/A'),
                 app.get('category', 'Unknown'), crawl_time)
            ]

            cursor.executemany(sql, batch_data)
            connection.commit()
            print(f"成功插入/更新 {app['app_name']}\n")
    except Exception as e:
        print(f"数据库操作失败: {str(e)}")
    finally:
        if connection:
            connection.close()


def select_from_database(app_name):
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)

        with connection.cursor() as cursor:
            # 使用参数化查询防止SQL注入
            sql = """
                SELECT * 
                FROM t_google_apks 
                WHERE app_name = %s
            """
            # 执行查询（自动提交事务）
            cursor.execute(sql, (app_name,))

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


def main():
    # 定义目录路径
    directory = "/Users/cuichenhui/Documents/local-repositories/llm-based-text-input-generation/scripts/downloads"  # 替换为你的目录路径
    # 初始化结果列表

    finish_files = []
    # 遍历目录下的所有文件
    for filename in os.listdir(directory):
        all_lines = []
        if filename.endswith(".txt"):
            filepath = os.path.join(directory, filename)

            # 读取文件内容
            with open(filepath, "r", encoding="utf-8") as f:
                # 逐行读取并去除换行符
                lines = [line.strip() for line in f.readlines()]
                all_lines.extend(lines)

        print(f"{filename}")

        for app_name in all_lines:
            index = all_lines.index(app_name)
            print(f"{filename}---{index}/{len(all_lines)}")
            app = select_from_database(app_name)
            if len(app) != 0:
                print(f"涛哥哥已经写进去了，跳过{app_name}")
                continue

            app = get_apps_from_google_play(app_name)
            save_to_database(app)
            time.sleep(10)
        finish_files.append(filename)



        print(f"当前已经完成：{finish_files}")


if __name__ == "__main__":
    main()
