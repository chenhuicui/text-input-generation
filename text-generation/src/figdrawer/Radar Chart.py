import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import mysql.connector
import json
import ast
import os
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
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

    # 创建4×8布局的大图 - 调整高度为12英寸以保持正方形比例
    fig = plt.figure(figsize=(24, 12))  # 高度从14减小到12，保持正方形比例
    gs = GridSpec(4, 8, figure=fig, hspace=0.5, wspace=0.2)  # 增加行间距

    # 为每个应用创建子图
    for i, app_id in enumerate(sorted_app_ids):
        row_idx = i // 8
        col_idx = i % 8

        # 如果超过31个应用，停止绘制
        if i >= 31:
            break

        # 创建主正方形子图
        ax_main = fig.add_subplot(gs[row_idx, col_idx])
        ax_main.set_box_aspect(1)  # 确保子图为正方形

        # 在正方形内创建左右两个子区域
        inner_gs = GridSpecFromSubplotSpec(1, 2, subplot_spec=gs[row_idx, col_idx], wspace=0)
        ax_left = fig.add_subplot(inner_gs[0])  # 左边：psi分布
        ax_right = fig.add_subplot(inner_gs[1])  # 右边：通过率折线

        # 获取当前应用的数据
        app_tau_data = final_tau_df[final_tau_df['app_id'] == app_id].sort_values('tau')
        app_tau_data['tau'] = app_tau_data['tau'].astype(int)
        d = app_info_df[app_info_df['app_id'] == app_id]['d'].values[0]

        # 获取当前应用的关键性数据
        app_criticality = final_criticality_df[
            final_criticality_df['app_id'] == app_id].copy()

        # 归一化ψ值
        if not app_criticality.empty:
            min_psi = app_criticality['criticality'].min()
            max_psi = app_criticality['criticality'].max()
            range_psi = max_psi - min_psi

            if range_psi == 0:
                app_criticality.loc[:, 'normalized_psi'] = 1.0
            else:
                app_criticality.loc[:, 'normalized_psi'] = (app_criticality['criticality'] - min_psi) / range_psi

            app_criticality = app_criticality.sort_values('normalized_psi', ascending=False)
            app_criticality.loc[:, 'rank'] = range(1, len(app_criticality) + 1)
        else:
            app_criticality = pd.DataFrame()

            # 简化应用ID显示（换行展示完整ID）
        if len(app_id) <= 33:
            # 如果ID较短，直接显示完整ID
            display_id = app_id
        else:
            # 如果ID较长，分成两行显示
            display_id = app_id[:33]  # 第一行显示前30个字符
            if len(app_id) > 33:
                # 第二行显示剩余部分（最多30个字符）
                remaining = app_id[33:]
                display_id += "\n" + remaining

        # === 左边：归一化psi分布（泡泡图） ===
        if not app_criticality.empty:
            # 绘制泡泡图
            scatter = ax_left.scatter(
                x=app_criticality['rank'],
                y=app_criticality['normalized_psi'],
                s=30,
                c='#ff0000',
                alpha=0.7,
                edgecolors='k',
                linewidths=0.5
            )

        # 设置左侧子图
        ax_left.set_ylim(-0.05, 1.05)
        ax_left.set_xlim(0.5, d + 0.5)
        ax_left.set_xticks(range(1, d + 1))
        ax_left.grid(ls='--', alpha=0.3)

        ax_left.set_xlabel("$c_i$", fontsize=10, fontname='Times New Roman', math_fontfamily='stix', labelpad=-2)

        # 设置Y轴标签 - 只保留第一列
        if col_idx == 0:
            ax_left.set_ylabel(r"Normalized $\Psi_{\mathrm{norm}}(c_i)$", fontsize=10, fontname='Times New Roman', math_fontfamily='stix',
                               color='#ff0000')
        else:
            # 非第一列，隐藏Y轴标签
            ax_left.set_ylabel("")
            ax_left.set_yticklabels([])

        # 设置刻度字体
        ax_left.tick_params(axis='x', labelsize=10, labelfontfamily='Times New Roman')
        ax_left.tick_params(axis='y', labelsize=10, labelfontfamily='Times New Roman', colors='r')

        # === 右边：通过率折线图 ===
        # 绘制折线图
        line = ax_right.plot(
            app_tau_data['tau'],
            app_tau_data['val'],
            'b-',
            marker='o',
            markersize=3,
            linewidth=1.2
        )

        # 创建右侧Y轴（PPTR通过率）并设置20%递增
        ax_right_pptr = ax_right.twinx()
        ax_right_pptr.set_ylim(-0.05, 1.05)

        # 设置20%递增的刻度和标签
        pptr_ticks = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        ax_right_pptr.set_yticks(pptr_ticks)
        ax_right_pptr.set_yticklabels(['0%', '20%', '40%', '60%', '80%', '100%'])
        ax_right_pptr.tick_params(axis='y', labelsize=10, labelfontfamily='Times New Roman', colors='b')

        # 设置右侧Y轴标签 - 只保留最后一列
        if col_idx == 7 or (col_idx == 6 and row_idx == 3):  # 最后一列显示标签
            ax_right_pptr.set_ylabel("Page-Pass-Through Rates", fontsize=10, fontname='Times New Roman', color='b')
            ax_right.set_yticks([])
            ax_right.set_yticklabels([])
        else:
            # 非最后一列，隐藏Y轴标签
            ax_right_pptr.set_ylabel("")
            ax_right_pptr.set_yticklabels([])
            # 隐藏刻度线
            ax_right.set_yticks([])
            ax_right.set_yticklabels([])

        # 设置右侧子图
        ax_right.set_ylim(-0.05, 1.05)
        ax_right.set_xlim(-0.5, d + 0.5)
        ax_right.set_xticks(range(0, d + 1))

        # 隐藏左侧Y轴刻度标签
        ax_right.set_yticklabels([])

        # 设置网格
        ax_right.grid(ls='--', alpha=0.3)
        # ax_right_pptr.grid(visible=False)

        ax_right.set_xlabel("τ", fontsize=10, fontname='Times New Roman', style='italic', labelpad=-2)

        # 设置刻度字体
        ax_right.tick_params(axis='x', labelsize=10, labelfontfamily='Times New Roman')

        # 设置主标题
        ax_main.set_title(f'{display_id}\n(app {i+1}, $d={d}$)',
                          fontsize=10,
                          fontname='Times New Roman',
                          math_fontfamily='stix')

        ax_main.axis('off')  # 隐藏主图的轴

    # 添加图例（在图形底部）
    from matplotlib.lines import Line2D

    legend_elements = [
        Line2D([0], [0], marker='o', color='w', markeredgecolor='black', markerfacecolor='#ff0000', markersize=10,
               label=r"Normalized $\Psi_{\mathrm{norm}}(c_i)$"),
        Line2D([0], [0], color='b', marker='o', linestyle='-', markersize=5, label='Page-Pass-Through Rates'),
    ]

    # 在图形底部中央添加图例
    fig.legend(handles=legend_elements, loc='lower center',
               bbox_to_anchor=(0.88, 0.15), ncol=1, fontsize=10,
               prop={'family': 'Times New Roman', 'math_fontfamily': 'stix'},
               edgecolor='white', framealpha=0.5)

    # 调整布局 - 增加顶部空间
    plt.tight_layout(rect=[0, 0.03, 1, 0.97], h_pad=0.5, w_pad=0.5)

    # 保存图表
    chart_path = os.path.join(OUTPUT_DIR, "result_2.2_apps_tau_distribution.pdf")
    plt.savefig(chart_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"已保存所有应用的组件分析图表: {chart_path}")
else:
    print("没有可用的τ值数据")

print("\n处理完成!")
