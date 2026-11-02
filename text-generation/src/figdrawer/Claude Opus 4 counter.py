import pymysql
import os
from anthropic import Anthropic
from tqdm import tqdm
import csv
from datetime import datetime

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

# 初始化Anthropic客户端
anthropic = Anthropic()  # 替换为你的API密钥


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


def calculate_tokens(text):
    """使用Anthropic API计算文本的token数量"""
    if not text:
        return 0
    try:
        # 正确调用messages.count_tokens方法
        response = anthropic.messages.count_tokens(
            model="claude-opus-4-20250514",
            messages=[{"role": "user", "content": text}]
        )
        return response.input_tokens
    except Exception as e:
        print(f"计算token时出错: {e}")
        # 回退到近似计算（1 token ≈ 4个英文字符）
        return len(text) // 4


def calculate_algo1(records):
    """计算算法1的token数量"""
    print("计算算法1的token数量...")
    results = []
    for record in tqdm(records, desc="算法1进度"):
        app_id = record[0]
        # 拼接所有字段
        combined = ''.join(str(field) for field in record[1:] if field is not None)
        token_count = calculate_tokens(combined)
        results.append((app_id, token_count))
    return results


def calculate_algo2(records):
    """计算算法2的token数量"""
    print("计算算法2的token数量...")
    results = []
    for record in tqdm(records, desc="算法2进度"):
        app_id = record[0]
        # 部分1: 三个字段的拼接
        combined_fields = ''.join(str(field) for field in record[1:] if field is not None)
        field_tokens = calculate_tokens(combined_fields)

        # 部分2: XML文件内容
        xml_tokens = 0
        xml_path = os.path.join(xml_dir, f"hierarchy_{app_id}.xml")
        if os.path.exists(xml_path):
            try:
                with open(xml_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
                    xml_tokens = calculate_tokens(xml_content)
            except Exception as e:
                print(f"处理XML文件 {xml_path} 时出错: {e}")
        else:
            print(f"警告: XML文件不存在: {xml_path}")

        # 部分3: 固定提示文本
        prompt_tokens = calculate_tokens(hierarchy_prompt)

        # 总和
        total_tokens = field_tokens + xml_tokens + prompt_tokens
        results.append((app_id, total_tokens, field_tokens, xml_tokens, prompt_tokens))
    return results


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
    print(f"Claude Opus 4 Token 计数结果")
    print(f"应用数量: {total_records}")
    print(f"算法1平均token数: {algo1_avg:.2f}")
    print(f"算法2平均token数: {algo2_avg:.2f}")
    print(f"固定提示文本token数: {calculate_tokens(hierarchy_prompt)}")
    print("=" * 50)

    # 准备文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = f"claude_opus4_token_results_{timestamp}.csv"

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

    # 成本估算（基于2025年7月Claude Opus定价）
    input_cost_per_million = 15.00  # 每百万输入token $15
    output_cost_per_million = 75.00  # 每百万输出token $75

    # 假设每个应用需要处理算法2的token数（输入）并生成100个token的输出
    total_input_tokens = algo2_avg * total_records
    total_output_tokens = 100 * total_records

    estimated_input_cost = (total_input_tokens / 1_000_000) * input_cost_per_million
    estimated_output_cost = (total_output_tokens / 1_000_000) * output_cost_per_million

    print("\n成本估算:")
    print(f"总输入token: {total_input_tokens:.0f} (${estimated_input_cost:.2f})")
    print(f"总输出token: {total_output_tokens:.0f} (${estimated_output_cost:.2f})")
    print(f"总成本估算: ${estimated_input_cost + estimated_output_cost:.2f}")

    print("计算完成!")


if __name__ == "__main__":
    main()