import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import mysql.connector

# 设置Times New Roman字体
plt.rcParams['font.family'] = 'serif'
plt.rcParams['font.serif'] = ['Times New Roman']
plt.rcParams['axes.unicode_minus'] = False

# 数据库连接配置
db_config = {
    'host': '101.201.82.24',  # 数据库主机地址
    'user': 'must_lab_db_user',  # 数据库用户名
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',  # 数据库密码
    'database': 'must_lab_db'  # 数据库名称
}

# 建立数据库连接
conn = mysql.connector.connect(**db_config)
query = "SELECT pk_id, model_type, val as rating FROM t_answer_chat where prompt_structure in (0)"
df = pd.read_sql(query, conn)
conn.close()

# 模型名称映射
model_mapping = {
    'gpt-4o': 'GPT-4o',
    'Deepseek-V1': 'DeepSeek-\nR1',
    'Grok_3': 'Grok-3',
    'CLAUDE_OPUS_4': 'Claude-\nOpus-4',
    'GLM-4P': 'GLM-4-\nPlus',
    'Baichuan4': 'Baichuan-4',
    'SPARK_4': 'Spark-4',
    'LLAMA_4_MAVERICK_INSTRUCT': 'LLaMa-4'
}

# 应用映射并过滤
df['model'] = df['model_type'].map(model_mapping)
df = df.dropna(subset=['model'])

# 指定模型顺序
model_order = ['GPT-4o', 'DeepSeek-\nR1', 'Grok-3', 'Claude-\nOpus-4',
               'GLM-4-\nPlus', 'Baichuan-4', 'Spark-4', 'LLaMa-4']

# 只保留指定模型的记录
df = df[df['model'].isin(model_order)]

# 确保评分在有效范围内
df['rating'] = df['rating'].clip(1, 5)

# 定义评分标签
rating_labels = {
    1: '1: Strongly Disagree',
    2: '2: Disagree',
    3: '3: Neutral',
    4: '4: Agree',
    5: '5: Strongly Agree'
}

# 计算每个模型各评分的比例
rating_counts = df.groupby(['model', 'rating']).size().unstack(fill_value=0)
rating_percent = rating_counts.div(rating_counts.sum(axis=1), axis=0) * 100

# 按模型顺序排序
rating_percent = rating_percent.reindex(model_order)

# 创建图形 - 设置4:3比例
plt.figure(figsize=(14, 10))  # 宽度12，高度9，比例4:3

# 定义颜色
colors = ['#FFD9D9', '#EED4EE', '#FFFFDC', '#DCEEFF', '#DCFFDC']  # 红到蓝渐变

# 绘制堆叠柱状图 - 使用较窄的柱子
bar_width = 0.7  # 较窄的柱子
bottom = np.zeros(len(model_order))
bars = []

plt.grid(axis='y', linestyle='--', alpha=0.5, zorder=1, linewidth=1)  # zorder设置为1，低于柱子的zorder

for i, rating in enumerate([1, 2, 3, 4, 5]):
    bar = plt.bar(
        model_order,
        rating_percent[rating],
        bottom=bottom,
        color=colors[i],
        edgecolor='black',
        width=bar_width,
        label=rating_labels[rating],
        zorder=2

    )
    bars.append(bar)
    bottom += rating_percent[rating]

# 添加比例标签 - 黑色字体，保留两位小数
for model_idx, model in enumerate(model_order):
    cumulative = 0
    for rating in [1, 2, 3, 4, 5]:
        value = rating_percent.loc[model, rating]
        if value > 0.1:  # 只显示大于0.1%的比例
            # 计算标签位置：贴着当前部分的底部
            y_pos = cumulative + 0.5  # 加0.5是为了避免标签被柱子边界切割

            plt.text(
                model_idx,
                y_pos,  # 使用计算出的位置
                f'{value:.2f}%',
                ha='center',
                va='bottom',  # 改为底部对齐
                fontsize=20,
                fontname='Times New Roman',
                color='black',
                # bbox=dict(
                #     facecolor='white',  # 添加白色背景提高可读性
                #     alpha=0.7,
                #     edgecolor='none',
                #     boxstyle='round,pad=0.1'
                # )
            )
        cumulative += value

# 添加平均评分线
for i, model in enumerate(model_order):
    avg_rating = df[df['model'] == model]['rating'].mean()
    # 将平均分转换为百分比位置（假设5分对应100%）
    avg_pos = avg_rating * 20  # 因为5分=100%，所以每分对应20%

    # 绘制虚线
    plt.plot(
        [i - bar_width / 2, i + bar_width / 2],
        [avg_pos, avg_pos],
        color='red',
        linewidth=1.5,
        linestyle='--',
    )

    # 在虚线上方添加标签
    label_y = avg_pos + 3  # 在虚线上方3个单位处
    if label_y > 100:  # 确保不超出图表范围
        label_y = avg_pos - 3

    # 添加标签，使用较大的字体和白色背景增强可读性
    plt.text(
        i,  # 居中显示
        label_y,
        f'{avg_rating:.2f}',
        ha='center',
        va='center',
        fontsize=20,
        fontname='Times New Roman',
        color='red',
        fontweight='bold'
    )
# 美化图表
plt.ylabel('Percentage Distribution of Testers\' 5-Point Likert Scale Evaluation (%)', fontsize=20,
           fontname='Times New Roman', labelpad=10)

# 设置X轴标签水平显示
plt.xticks(fontsize=20, fontname='Times New Roman', rotation=0)
plt.tick_params(axis='x', which='major', pad=10)  # 设置x轴主刻度标签的pad（距离）

# 设置Y轴
plt.yticks(np.arange(0, 101, 10), fontsize=20, fontname='Times New Roman')
plt.ylim(0, 100)
# plt.grid(axis='y', linestyle='--', alpha=0.5, zorder=0)

# 加粗图表四周的边框线
ax = plt.gca()
for spine in ax.spines.values():
    spine.set_linewidth(1)  # 设置边框线宽为2
    spine.set_zorder(5)  # 确保边框在最上层

# 添加图例（底部水平排列）
plt.legend(
    loc='upper center',
    bbox_to_anchor=(0.48, 1.1),  # 位于图表上方，水平居中，y轴位置在图表顶部以上1.15（单位是图表高度）
    ncol=5,
    frameon=True,
    prop={'family': 'Times New Roman', 'size': 18},
    edgecolor='white',
    framealpha=0.5
)

# 添加数据统计信息
num_responses = len(df)
num_testers = df['pk_id'].nunique()

# 调整布局
plt.tight_layout()
plt.subplots_adjust(bottom=0.2)  # 为图例和说明留出空间

plt.savefig('result_3.2_stacked_rating_distribution.pdf', bbox_inches='tight', dpi=300)
plt.show()
