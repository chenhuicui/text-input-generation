import pymysql
import os
import transformers
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

# DeepSeek tokenizer目录
DEEPSEEK_TOKENIZER_DIR = "./"


# 加载DeepSeek tokenizer
def load_tokenizer():
    print("加载DeepSeek tokenizer...")
    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            DEEPSEEK_TOKENIZER_DIR,
            trust_remote_code=True
        )
        print("Tokenizer加载成功!")
        return tokenizer
    except Exception as e:
        print(f"加载tokenizer失败: {e}")
        raise


# 全局tokenizer实例
tokenizer = load_tokenizer()


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
    """计算文本的token数量"""
    if not text:
        return 0
    return len(tokenizer.encode(text))


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
    print(f"应用数量: {total_records}")
    print(f"算法1平均token数: {algo1_avg:.2f}")
    print(f"算法2平均token数: {algo2_avg:.2f}")
    print(f"固定提示文本token数: {calculate_tokens(hierarchy_prompt)}")
    print("=" * 50)

    # 准备文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = f"deepseek_token_results_{timestamp}.csv"

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

    print("计算完成!")


if __name__ == "__main__":
    main()