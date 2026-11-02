import mysql.connector
import numpy as np
import pandas as pd
import numpy as np
from scipy.stats import kendalltau

# 数据库连接配置 (示例，请替换为实际配置)
db_config = {
    'host': '101.201.82.24',  # 数据库主机地址
    'user': 'must_lab_db_user',  # 数据库用户名
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',  # 数据库密码
    'database': 'must_lab_db'  # 数据库名称
}


def fetch_data():
    """从数据库获取数据并返回DataFrame"""
    conn = mysql.connector.connect(**db_config)
    query = """
        SELECT pk_id, AVG(val) as avg_score
        FROM t_answer_chat
        WHERE prompt_structure = 0
        GROUP BY pk_id, model_type
        ORDER BY pk_id, model_type
    """
    return pd.read_sql(query, conn)


def process_data(df):
    """处理数据为8x20的NumPy数组"""
    # 转换pk_id为q格式
    df['question'] = 'q' + df['pk_id'].astype(str)

    # 创建透视表 (每个模型一行，每个问题一列)
    pivot_df = df.pivot_table(index=df.groupby('pk_id').cumcount(),
                              columns='question',
                              values='avg_score',
                              aggfunc='first')

    # 按问题编号排序 (q1, q2, ..., q20)
    sorted_cols = sorted(pivot_df.columns, key=lambda x: int(x.replace('q', '')))
    pivot_df = pivot_df[sorted_cols]

    # 转换为NumPy数组
    return pivot_df.to_numpy()

    # 从数据库获取数据
df = fetch_data()

    # 检查数据量是否符合预期 (20问题×8模型=160行)
if len(df) != 160:
     print(f"警告: 数据量异常 ({len(df)}行)，应为160行")

    # 处理数据
data = process_data(df)

print(data)

# print(data)
#
# sum_w = 0
# count_w = 0
# p_value_w = 0
# for i in range(data.shape[0]):
#     for j in range(i + 1, data.shape[0]):
#         w, p_value = kendalltau(data[i], data[j],variant='b')
#         sum_w += w
#         count_w += 1
#         p_value_w += p_value
#         print(f"评委 {i+1} 和 评委 {j+1} 的 Kendall's W 系数: {w:.6f}, p值: {p_value:.6f}")
# print(count_w)
#
# # 计算data中每一个评委的平均值

#
# print(f"Kendall's W 系数: {sum_w/count_w:.6f}")
# print(f"Kendall's W 系数: {p_value_w/count_w:.6f}")
def kendall_w(data):
    """
    计算Kendall's W协同系数

    参数:
    data : 2D numpy数组或列表的列表
        数据矩阵，行表示评估者(m)，列表示样本(N)

    返回:
    W : float
        Kendall's W系数
    """
    # 转换为numpy数组
    data = np.array(data)
    m, N = data.shape  # m: 评估者数, N: 样本量

    # 步骤1: 为每个评估者的评分分配秩次
    ranks = np.zeros_like(data)
    for i in range(m):
        # 使用scipy的rankdata处理相同值（平局）的情况
        from scipy.stats import rankdata
        ranks[i] = rankdata(data[i], method='average')

    # 步骤2: 计算每个样本的总秩次R_i
    R_i = np.sum(ranks, axis=0)

    # 步骤3: 计算平均总秩次R_bar
    R_bar = np.mean(R_i)

    # 步骤4: 计算S = Σ(R_i - R_bar)^2
    S = np.sum((R_i - R_bar) ** 2)

    # 步骤5: 计算Kendall's W
    denominator = m ** 2 * (N ** 3 - N)
    W = 12 * S / denominator

    return W

print(kendall_w(data))

mean_scores = np.mean(data, axis=1)
print(f"每个评委的平均评分:{mean_scores}")
#计算mean_scores的平均值
mean_w = np.mean(mean_scores)
print(f"所有评委的平均评分: {mean_w:.6f}")