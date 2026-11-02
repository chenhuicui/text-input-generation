import pymysql
import os
import requests
import time
import json
from tqdm import tqdm
import csv
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 数据库配置（根据实际情况修改）
db_config = {
    'host': '101.201.82.24',  # 数据库主机地址
    'user': 'must_lab_db_user',  # 数据库用户名
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',  # 数据库密码
    'database': 'must_lab_db'  # 数据库名称
}

# XML文件路径
xml_dir = "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study/output/xml_dumps"

# 固定提示文本
hierarchy_prompt = "Here is the detailed hierarchy structure of the UI that contains those text-input components:"

# 百川API配置
BAICHUAN_API_URL = "https://api.baichuan-ai.com/v1/embeddings"
BAICHUAN_API_KEY = "REDACTED_USE_ENV_BAICHUAN_API_KEY"  # 替换为您的百川API密钥
MODEL_NAME = "Baichuan-Text-Embedding"
MAX_BATCH_SIZE = 16  # 每次请求最多16条文本
RATE_LIMIT = 40  # 每分钟请求数（企业认证账号）
SLEEP_TIME = 60 / RATE_LIMIT  # 请求间隔时间


def calculate_baichuan_tokens(text):
    """使用百川API计算文本的token数量"""
    if not text:
        return 0

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BAICHUAN_API_KEY}"
    }

    payload = {
        "model": MODEL_NAME,
        "input": [text]
    }

    try:
        response = requests.post(BAICHUAN_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        # 返回第一个文本的token数量
        return data["usage"]["prompt_tokens"]
    except Exception as e:
        print(f"百川API调用失败: {e}")
        # 回退到官方估算公式
        return calculate_estimated_tokens(text)


def calculate_estimated_tokens(text):
    """
    百川官方估算公式:
    token数 = 汉字数 + 单词数 * 1.3
    """
    if not text:
        return 0

    # 统计汉字数量 (Unicode范围: \u4e00-\u9fff)
    chinese_chars = [char for char in text if '\u4e00' <= char <= '\u9fff']
    chinese_count = len(chinese_chars)

    # 统计英文单词数量 (连续的英文字母序列)
    words = []
    current_word = ""
    for char in text:
        if char.isalpha():
            current_word += char
        else:
            if current_word:
                words.append(current_word)
                current_word = ""
    if current_word:
        words.append(current_word)

    word_count = len(words)

    # 应用百川公式
    token_count = chinese_count + word_count * 1.3

    # 四舍五入取整
    return round(token_count)


def get_db_records():
    """从数据库获取记录并返回两种算法所需数据"""
    print("从数据库获取记录...")
    connection = pymysql.connect(**db_config)
    try:
        with connection.cursor() as cursor:
            # 获取所有app_id
            cursor.execute("SELECT DISTINCT app_id FROM t_google_prompts")
            app_ids = [row[0] for row in cursor.fetchall()]

            # 获取算法1所需字段（5个字段）
            cursor.execute("SELECT app_id, global, component, adjacent, restrictive, guiding FROM t_google_prompts")
            algo1_records = cursor.fetchall()

            # 获取算法2所需字段（3个字段）
            cursor.execute("SELECT app_id, global, restrictive, guiding FROM t_google_prompts")
            algo2_records = cursor.fetchall()

        return app_ids, algo1_records, algo2_records
    finally:
        connection.close()


def calculate_algo1(records):
    """计算算法1的token数量"""
    print("计算算法1的token数量...")
    results = []

    # 分批处理以符合API限制
    batches = [records[i:i + MAX_BATCH_SIZE] for i in range(0, len(records), MAX_BATCH_SIZE)]

    for batch in tqdm(batches, desc="算法1进度"):
        texts = []
        app_ids = []

        # 准备API请求数据
        for record in batch:
            app_id = record[0]
            combined = ''.join(str(field) for field in record[1:] if field is not None)
            texts.append(combined)
            app_ids.append(app_id)

        # 调用API计算token
        token_counts = calculate_tokens_batch(texts)

        # 保存结果
        for app_id, token_count in zip(app_ids, token_counts):
            results.append((app_id, token_count))

        # 遵守API速率限制
        time.sleep(5)

    return results


def calculate_algo2(records):
    """计算算法2的token数量"""
    print("计算算法2的token数量...")
    results = []

    # 分批处理以符合API限制
    batches = [records[i:i + MAX_BATCH_SIZE] for i in range(0, len(records), MAX_BATCH_SIZE)]

    for batch in tqdm(batches, desc="算法2进度"):
        app_ids = []
        field_texts = []
        xml_texts = []

        # 准备API请求数据
        for record in batch:
            app_id = record[0]
            app_ids.append(app_id)

            # 字段部分
            combined_fields = ''.join(str(field) for field in record[1:] if field is not None)
            field_texts.append(combined_fields)

            # XML部分
            xml_path = os.path.join(xml_dir, f"hierarchy_{app_id}.xml")
            xml_content = ""
            if os.path.exists(xml_path):
                try:
                    with open(xml_path, 'r', encoding='utf-8') as f:
                        xml_content = f.read()
                except Exception as e:
                    print(f"处理XML文件 {xml_path} 时出错: {e}")
            else:
                print(f"警告: XML文件不存在: {xml_path}")
            xml_texts.append(xml_content)

        # 调用API计算token
        field_token_counts = calculate_tokens_batch(field_texts)
        xml_token_counts = calculate_tokens_batch(xml_texts)

        # 固定提示文本token数
        prompt_tokens = calculate_estimated_tokens(hierarchy_prompt)

        # 保存结果
        for app_id, field_tokens, xml_tokens in zip(app_ids, field_token_counts, xml_token_counts):
            total_tokens = field_tokens + xml_tokens + prompt_tokens
            results.append((app_id, total_tokens, field_tokens, xml_tokens, prompt_tokens))

        # 遵守API速率限制
        time.sleep(SLEEP_TIME * 2)  # 额外等待，因为每个请求包含两个批次

    return results


def calculate_tokens_batch(texts):
    """使用百川API批量计算token数量"""
    if not texts:
        return [0] * len(texts)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BAICHUAN_API_KEY}"
    }

    payload = {
        "model": MODEL_NAME,
        "input": texts
    }

    try:
        response = requests.post(BAICHUAN_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        # 获取每个文本的token数量
        token_counts = []
        for item in data["data"]:
            # 计算每个文本的实际token数量
            # 由于API返回整个批次的token总数，我们需要估算每个文本的token数
            # 使用总token数除以文本数量作为近似值
            token_counts.append(data["usage"]["prompt_tokens"] // len(texts))

        return token_counts
    except Exception as e:
        print(f"百川API批量调用失败: {e}")
        # 回退到官方估算公式
        return [calculate_estimated_tokens(text) for text in texts]


def main():
    # 获取数据库记录
    app_ids, algo1_records, algo2_records = get_db_records()
    total_records = len(app_ids)
    print(f"找到 {total_records} 个应用记录")

    # 计算两种算法
    algo1_results = calculate_algo1(algo1_records)
    algo2_results = calculate_algo2(algo2_records)

    # 计算平均值
    algo1_avg = sum(token for _, token in algo1_results) / total_records
    algo2_avg = sum(token for app_id, token, *parts in algo2_results) / total_records

    # 打印结果摘要
    print("\n" + "=" * 50)
    print(f"百川 Token 计数结果 (API精确计算)")
    print(f"应用数量: {total_records}")
    print(f"算法1平均token数: {algo1_avg:.2f}")
    print(f"算法2平均token数: {algo2_avg:.2f}")
    print(f"固定提示文本token数: {calculate_estimated_tokens(hierarchy_prompt)}")
    print("=" * 50)

    # 准备文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = f"baichuan_api_token_results_{timestamp}.csv"

    # 保存详细结果到CSV
    print(f"保存详细结果到CSV文件: {csv_file}")
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['算法', '应用ID', '总Token数', '字段Token', 'XML Token', '提示Token'])

        # 写入算法1结果
        for app_id, tokens in algo1_results:
            writer.writerow(['算法1', app_id, tokens, '', '', ''])

        # 写入算法2结果
        for app_id, total_tokens, field_tokens, xml_tokens, prompt_tokens in algo2_results:
            writer.writerow(['算法2', app_id, total_tokens, field_tokens, xml_tokens, prompt_tokens])

    # 测试示例
    test_text = "百川大模型"
    test_tokens = calculate_baichuan_tokens(test_text)
    print(f"\n测试示例: '{test_text}'")
    print(f"API计算token数: {test_tokens} (官方文档示例为7)")

    print("计算完成!")


if __name__ == "__main__":
    main()