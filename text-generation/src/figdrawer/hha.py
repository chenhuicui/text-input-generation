import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import mysql.connector
import json
import ast
import os
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import MaxNLocator

# 数据库配置
DB_CONFIG = {
    'host': '101.201.82.24',  # 数据库主机地址
    'user': 'must_lab_db_user',  # 数据库用户名
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',  # 数据库密码
    'database': 'must_lab_db'  # 数据库名称
}

# 输出目录
OUTPUT_DIR = "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study/src/figdrawer"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# 从MySQL加载真实数据
def load_data():
    """从MySQL数据库加载真实数据"""
    conn = mysql.connector.connect(**DB_CONFIG)
    print("成功连接到MySQL数据库!")

    query = f"""
        SELECT 
            app_id,
            model_type as model,
            seq,
            val,
            prompt_structure,
            tao as tau,
            tao_seq as tau_seq,
            component_num,
            texts as combination
        FROM t_google_component_results
        """

    df = pd.read_sql_query(query, conn)
    conn.close()

    required_columns = ['app_id', 'tau', 'tau_seq', 'val', 'seq', 'component_num', 'combination']
    missing = set(required_columns) - set(df.columns)
    if missing:
        raise ValueError(f"表中缺少必要字段: {missing}")

    print(f"成功加载数据! 记录数: {len(df)}")
    print(f"唯一页面数: {df['app_id'].nunique()}")
    return df


# 解析组合JSON的函数
def parse_combination(comb_json):
    """解析组合JSON，返回填充的组件列表和所有组件列表"""
    try:
        if isinstance(comb_json, str):
            comb_dict = json.loads(comb_json)
        else:
            comb_dict = comb_json

        filled_components = [comp for comp, value in comb_dict.items() if value != ""]
        all_components = list(comb_dict.keys())
        return filled_components, all_components
    except (json.JSONDecodeError, TypeError):
        try:
            if isinstance(comb_json, str):
                comb_dict = ast.literal_eval(comb_json)
                filled_components = [comp for comp, value in comb_dict.items() if value != ""]
                all_components = list(comb_dict.keys())
                return filled_components, all_components
        except:
            return [], []
    return [], []


# 加载数据
print("正在从MySQL数据库加载数据...")
df = load_data()


def calculate_component_criticality(group):
    """计算组件的综合关键性得分（跨模型和提示结构）"""
    if group.empty:
        return pd.DataFrame()

    d = group['component_num'].iloc[0]  # 系统总组件数
    component_scores = {}  # 存储每个组件的所有得分

    # 按模型、提示结构和组合状态分组
    grouped = group.groupby(['model', 'prompt_structure', 'combination'])

    # 计算每组(模型, 提示结构, 组合)的平均值
    avg_data = []
    for (model, prompt, comb), sub_group in grouped:
        avg_val = sub_group['val'].mean()
        tau_val = sub_group['tau'].iloc[0]  # tau值相同

        # 计算成功次数（val=1的次数）
        success_count = sub_group['val'].sum()

        avg_data.append({
            'model': model,
            'prompt_structure': prompt,
            'combination': comb,
            'val': avg_val,
            'tau': tau_val,
            'success_count': success_count,
            'total_count': len(sub_group)
        })

    avg_df = pd.DataFrame(avg_data)

    # 按模型和提示结构分组计算关键性
    model_prompt_groups = avg_df.groupby(['model', 'prompt_structure'])

    for (model, prompt), model_group in model_prompt_groups:
        mapping = {}
        comp_states = {}

        for idx, row in model_group.iterrows():
            comp_list, _ = parse_combination(row['combination'])
            comp_set = frozenset(comp_list)
            mapping[comp_set] = {
                'val': row['val'],
                'success_count': row['success_count'],
                'total_count': row['total_count']
            }

            for comp in comp_set:
                comp_states.setdefault(comp, set()).add(comp_set)

        if not comp_states:
            continue

        # 计算每个组件的关键性得分
        for comp in comp_states.keys():
            total_sum = 0.0
            count = 0

            # 遍历包含当前组件的所有状态
            for s_set in comp_states[comp]:
                if s_set not in mapping:
                    continue

                val_s = mapping[s_set]['val']
                tau_s = len(s_set)

                # 构建移除当前组件后的状态集合
                s_set_without = s_set - {comp}

                # 查找移除组件后的状态
                if s_set_without in mapping:
                    val_without = mapping[s_set_without]['val']
                else:
                    val_without = 0  # 状态不存在，设为0

                # 计算贡献值
                contribution = (val_s - val_without) * (tau_s / d)
                total_sum += contribution
                count += 1

            # 计算当前模型和提示结构下的psi值
            if count > 0:
                psi = total_sum / count
            else:
                psi = 0.0

            # 存储组件得分
            if comp not in component_scores:
                component_scores[comp] = []
            component_scores[comp].append(psi)

    # 计算每个组件的综合得分
    results = []
    for comp, scores in component_scores.items():
        # 使用简单平均
        avg_score = sum(scores) / len(scores)

        results.append({
            'component': comp,
            'criticality': avg_score,
            'score_count': len(scores),
            'min_score': min(scores),
            'max_score': max(scores),
            'std_dev': np.std(scores) if len(scores) > 1 else 0
        })

    return pd.DataFrame(results)


# 收集所有关键性得分和tau数据
all_criticality = []
all_tau_data = []
app_info = []  # 存储应用信息用于排序

print(f"开始处理{df['app_id'].nunique()}个页面...")
for app_id, app_data in df.groupby('app_id'):
    d = app_data['component_num'].iloc[0]
    print(f"处理页面: {app_id}, 组件数: {d}")

    # 存储应用信息
    app_info.append({
        'app_id': app_id,
        'd': d
    })

    # 计算组件关键性
    criticality_df = calculate_component_criticality(app_data)

    # 收集关键性数据
    if not criticality_df.empty:
        criticality_df['app_id'] = app_id
        all_criticality.append(criticality_df)

    # 收集tau数据
    tau_group = app_data.groupby('tau')
    tau_success = tau_group['val'].mean().reset_index()
    tau_counts = tau_group.size().reset_index(name='total_count')
    tau_success_counts = tau_group['val'].sum().reset_index(name='success_count')

    # 合并数据
    tau_data = pd.merge(tau_success, tau_counts, on='tau')
    tau_data = pd.merge(tau_data, tau_success_counts, on='tau')
    tau_data['app_id'] = app_id
    all_tau_data.append(tau_data)

# 合并所有关键性数据并保存
if all_criticality:
    final_criticality_df = pd.concat(all_criticality, ignore_index=True)
    csv_path = os.path.join(OUTPUT_DIR, "component_criticality_scores.csv")
    final_criticality_df.to_csv(csv_path, index=False)
    print(f"已保存组件关键性得分表格: {csv_path}")
else:
    print("没有可用的关键性得分数据")

# 合并所有tau数据
if all_tau_data:
    final_tau_df = pd.concat(all_tau_data, ignore_index=True)

    # 创建应用信息DataFrame并排序
    app_info_df = pd.DataFrame(app_info)
    app_info_df = app_info_df.sort_values('d')

    # 获取排序后的app_id列表
    sorted_app_ids = app_info_df['app_id'].tolist()
    num_apps = len(sorted_app_ids)
    print(f"共有{num_apps}个应用需要绘制图表，按组件数d排序")

    # 创建4×8布局的大图（32个子图位置）
    fig = plt.figure(figsize=(24, 14))  # 减小高度以减小行间距
    gs = GridSpec(4, 8, figure=fig, hspace=0.15, wspace=0.2)  # 减小行间距和列间距

    # 为每个应用创建子图
    for i, app_id in enumerate(sorted_app_ids):
        row = i // 8
        col = i % 8

        # 如果超过31个应用，停止绘制
        if i >= 31:
            break

        ax = fig.add_subplot(gs[row, col])


        # 获取当前应用的数据
        app_tau_data = final_tau_df[final_tau_df['app_id'] == app_id].sort_values('tau')

        # 确保tau值为整数
        app_tau_data['tau'] = app_tau_data['tau'].astype(int)

        # 获取组件数d
        d = app_info_df[app_info_df['app_id'] == app_id]['d'].values[0]

        # 简化应用ID显示（换行展示完整ID）
        if len(app_id) <= 33:
            # 如果ID较短，直接显示完整ID
            display_id = app_id
        else:
            # 如果ID较长，分成两行显示
            display_id = app_id[:33] # 第一行显示前30个字符
            if len(app_id) > 33:
                # 第二行显示剩余部分（最多30个字符）
                remaining = app_id[33:]
                display_id += "\n" + remaining

        # 创建双Y轴
        ax2 = ax.twinx()
        ax.set_zorder(ax2.get_zorder() + 1)  # 将ax放在ax2之上
        ax.patch.set_visible(False)  # 使ax的背景透明，这样ax2的内容就不会被遮挡
        # 绘制成功率折线图（蓝色）
        line = ax.plot(
            app_tau_data['tau'],
            app_tau_data['val'],
            'bo-',
            markersize=3,  # 减小标记大小
            linewidth=1.2,  # 减小线宽
            label='Page-Pass-Through Rates',
            zorder=3  # 确保折线在柱状图上方
        )

        # 添加网格
        ax.grid(ls='--', alpha=0.3, zorder=1)

        # 绘制测试用例数量柱状图（浅绿色）
        bars_total = ax2.bar(
            app_tau_data['tau'],
            app_tau_data['total_count'],
            color='#90EE90',  # 浅绿色
            alpha=0.7,
            label='Total Tests',
            width=0.6,
        )

        # 绘制成功用例数量柱状图（深绿色）
        bars_success = ax2.bar(
            app_tau_data['tau'],
            app_tau_data['success_count'],
            color='#32CD32',  # 深绿色
            alpha=0.8,
            label='Successful Tests',
            width=0.6
        )



        # 设置标题和标签 - 使用Times New Roman字体
        ax.set_title(f'{display_id}\n($d={d}$)',
                     fontsize=9,
                     fontname='Times New Roman',
                     math_fontfamily='stix')
        ax.set_ylim(0, 1)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.0%}'))  # 将Y值转换为百分比格式

        # 设置X轴标签 - 使用LaTeX样式显示斜体τ
        ax.set_xlabel("τ",  # 使用普通字符串
                      fontsize=10,
                      fontname='Times New Roman',
                      style='italic')  # 设置为斜体
        # 设置X轴只显示整数
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))

        # 设置Y轴标签
        if col == 0:
            ax.set_ylabel('Page-Pass-Through Rates', fontsize=9, fontname='Times New Roman', color='b')
            ax.tick_params(axis='y', labelsize=8, labelcolor='b')
        else:
            ax.set_yticklabels([])

        DARK_GREEN = '#006400'  # 深绿色

        # 设置Y轴标签 - 确保所有子图都有右侧Y轴标签
        if col == 7 or (row == 3 and col == 6):  # 最右侧或最后一行最后一列
            ax2.set_ylabel('Test Count', fontsize=9, fontname='Times New Roman', color=DARK_GREEN)
            ax2.tick_params(axis='y', labelsize=9, labelcolor=DARK_GREEN)
        else:
            # 对于其他子图，显示刻度但不显示标签
            ax2.tick_params(axis='y', which='both', length=3, labelsize=9, labelcolor=DARK_GREEN)

        # 设置刻度标签字体
        ax.tick_params(axis='x', labelsize=9, labelfontfamily='Times New Roman')
        ax.tick_params(axis='y', labelsize=9, labelfontfamily='Times New Roman')
        ax2.tick_params(axis='y', labelsize=9, labelfontfamily='Times New Roman')



        # 手动设置子图宽高比为1:1
        ax.set_box_aspect(1)  # 正方形子图

    # 添加图例（在图形底部）
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    legend_elements = [
        Line2D([0], [0], color='b', marker='o', linestyle='-', markersize=5, label='Page-Pass-Through Rates'),
        Patch(facecolor='#90EE90', edgecolor='#90EE90', label='Total Tests'),
        Patch(facecolor='#32CD32', edgecolor='#32CD32', label='Successful Tests')
    ]

    # 添加图例在图形底部中央
    fig.legend(handles=legend_elements, loc='lower center',
               bbox_to_anchor=(0.87, 0.17), ncol=1, fontsize=10, prop={'family': 'Times New Roman'},
               edgecolor='white', framealpha=0.5)

    # 调整布局 - 为图例留出空间，并减小行间距
    plt.tight_layout(rect=[0, 0.03, 1, 0.98], h_pad=0.5, w_pad=0.5)

    # 保存图表
    chart_path = os.path.join(OUTPUT_DIR, "result_2.2_apps_tau_distribution.pdf")
    plt.savefig(chart_path, dpi=300, bbox_inches='tight')  # 提高DPI以获得更清晰的文字
    plt.close()
    print(f"已保存所有应用的τ值成功率图表: {chart_path}")
else:
    print("没有可用的τ值数据")

print("\n处理完成!")