import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.colors import LinearSegmentedColormap
import os
import json
import mysql.connector
from mysql.connector import Error
import ast
import matplotlib as mpl

# 设置全局字体大小
mpl.rcParams.update({'font.size': 12})

# 配置参数
# FIXED_MODEL = "Baichuan4"  # 固定模型类型
# FIXED_PROMPT = 0  # 固定提示结构 (0或5)
# FIX_APP = "com.nihaojewelry.nihao"  # 固定应用ID前缀
OUTPUT_DIR = "../com.nihaojewelry.nihao"  # 输出目录

# MySQL数据库配置
DB_CONFIG = {
    'host': '101.201.82.24',  # 数据库主机地址
    'user': 'must_lab_db_user',  # 数据库用户名
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',  # 数据库密码
    'database': 'must_lab_db'  # 数据库名称
}

# 创建输出目录
os.makedirs(OUTPUT_DIR, exist_ok=True)


# 1. 从MySQL加载真实数据
def load_data():
    """从MySQL数据库加载真实数据"""

    # 连接到MySQL数据库
    conn = mysql.connector.connect(**DB_CONFIG)
    print("成功连接到MySQL数据库!")

    # 构建查询
    query = f"""
        SELECT 
            app_id,
            model_type,
            seq,
            val,
            prompt_structure,
            tao as tau,
            tao_seq as tau_seq,
            component_num,
            texts as combination
        FROM t_google_component_results
        """

    # 执行查询
    df = pd.read_sql_query(query, conn)
    conn.close()

    # 验证必要字段
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
        # 尝试解析JSON
        if isinstance(comb_json, str):
            comb_dict = json.loads(comb_json)
        else:
            comb_dict = comb_json

        # 提取填充的组件 (值不为空的)
        filled_components = [comp for comp, value in comb_dict.items() if value != ""]

        # 所有组件列表
        all_components = list(comb_dict.keys())

        return filled_components, all_components

    except (json.JSONDecodeError, TypeError):
        # 如果解析失败，尝试安全解析
        try:
            if isinstance(comb_json, str):
                # 尝试安全解析类似字典的字符串
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


# 2. 计算组件关键性得分
# 2. 改进的组件关键性得分计算
def calculate_component_criticality(group):
    """计算组件的关键性得分（改进版）"""
    if group.empty:
        return pd.DataFrame()

    d = group['component_num'].iloc[0]

    # 步骤1: 收集所有组件
    all_components = set()
    for comb_json in group['combination'].unique():
        _, components = parse_combination(comb_json)
        all_components.update(components)

    if not all_components:
        return pd.DataFrame()

    results = []

    # 步骤2: 计算完整状态的成功率基线
    full_states = group[group['tau'] == d]
    if not full_states.empty:
        baseline_success = full_states['val'].mean()
    else:
        baseline_success = group['val'].mean()

    for comp in all_components:
        # 方法A: 直接对比法（更简单可靠）
        # 步骤A1: 收集包含该组件的所有状态
        with_component = group[group.apply(
            lambda row: comp in parse_combination(row['combination'])[0],
            axis=1
        )]

        # 步骤A2: 收集不包含该组件的所有状态
        without_component = group[group.apply(
            lambda row: comp not in parse_combination(row['combination'])[1],
            axis=1
        )]

        # 步骤A3: 计算成功率差异
        if not with_component.empty and not without_component.empty:
            success_with = with_component['val'].mean()
            success_without = without_component['val'].mean()
            delta = success_with - success_without
        else:
            delta = np.nan

        # 方法B: 基于完整状态的相对重要性
        # 步骤B1: 收集包含该组件的完整状态
        full_with_component = group[
            (group['tau'] == d) &
            group.apply(lambda row: comp in parse_combination(row['combination'])[0],
                        axis=1
                        )]

        # 步骤B2: 收集不包含该组件的完整状态（不可能，因为完整状态包含所有组件）
        # 因此我们使用最接近完整状态的状态
        near_full = group[group['tau'] == d - 1]
        if not near_full.empty:
            success_near_full = near_full['val'].mean()
        else:
            success_near_full = baseline_success

        # 步骤B3: 计算组件存在时的成功率提升
        if not full_with_component.empty:
            success_full = full_with_component['val'].mean()
            relative_boost = success_full - success_near_full
        else:
            relative_boost = np.nan

        # 综合评分
        if not np.isnan(delta) and not np.isnan(relative_boost):
            psi = 0.7 * delta + 0.3 * relative_boost
        elif not np.isnan(delta):
            psi = delta
        elif not np.isnan(relative_boost):
            psi = relative_boost
        else:
            psi = np.nan

        results.append({
            'component': comp,
            'criticality': psi,
            'success_with': success_with if 'success_with' in locals() else np.nan,
            'success_without': success_without if 'success_without' in locals() else np.nan,
            'delta': delta if 'delta' in locals() else np.nan
        })

    return pd.DataFrame(results)


# 3. 为每个app_id生成分析图表
print(f"开始为{df['app_id'].nunique()}个页面生成分析图表...")
for app_id, app_data in df.groupby('app_id'):
    d = app_data['component_num'].iloc[0]

    print(f"处理页面: {app_id}, 组件数: {d}")

    # 计算组件关键性
    criticality_df = calculate_component_criticality(app_data)

    # 创建图表
    plt.figure(figsize=(15, 10))
    plt.suptitle(f"Component Criticality: {app_id}\nComponents: {d}, Model: {00}, Prompt: {00}",
                 fontsize=16, y=0.98)

    # 图1: 组件关键性得分
    ax1 = plt.subplot(2, 2, 1)
    if criticality_df.empty or criticality_df['criticality'].isna().all():
        ax1.text(0.5, 0.5, "No valid data for criticality calculation",
                 ha='center', va='center')
        ax1.set_title('Component Criticality Scores')
    else:
        criticality_df = criticality_df.sort_values('criticality', ascending=False)
        criticality_df = criticality_df.dropna(subset=['criticality'])

        # 简化组件ID显示
        criticality_df['component_short'] = criticality_df['component'].apply(
            lambda x: x.split('/')[-1] if '/' in x else x
        )

        # 创建颜色映射
        colors = []
        for score in criticality_df['criticality']:
            if score > 0.7:
                colors.append('#1a9850')  # 关键
            elif score > 0.3:
                colors.append('#91cf60')  # 增强
            elif score < -0.3:
                colors.append('#d73027')  # 干扰
            else:
                colors.append('#bababa')  # 中性

        # 绘制柱状图
        bars = ax1.bar(criticality_df['component_short'], criticality_df['criticality'], color=colors)
        ax1.axhline(0.7, color='#006837', ls='--', label='Critical (ψ>0.7)')
        ax1.axhline(0.3, color='#369b36', ls=':', label='Enhancing (0.3≤ψ≤0.7)')
        ax1.axhline(-0.3, color='#a50026', ls='--', label='Disruptive (ψ<-0.3)')
        ax1.axhline(0, color='k', lw=0.5)

        ax1.set_title('Component Criticality Scores')
        ax1.set_xlabel('Component')
        ax1.set_ylabel('Criticality Score (ψ)')
        ax1.set_ylim(-1.1, 1.1)
        ax1.tick_params(axis='x', rotation=45)
        ax1.legend(loc='best')

        # 添加数值标签
        for bar in bars:
            height = bar.get_height()
            va = 'bottom' if height > 0 else 'top'
            ax1.text(bar.get_x() + bar.get_width() / 2, height,
                     f'{height:.2f}', ha='center', va=va, fontsize=9)

    # 图2: τ值成功率趋势
    ax2 = plt.subplot(2, 2, 2)
    tau_success = app_data.groupby('tau')['val'].mean().reset_index()
    tau_counts = app_data['tau'].value_counts().sort_index().reset_index()
    tau_counts.columns = ['tau', 'count']
    tau_data = pd.merge(tau_success, tau_counts, on='tau')

    # 双轴图表
    ax2.plot(tau_data['tau'], tau_data['val'], 'bo-', label='Success Rate')
    ax2.set_xlabel('τ (Filled Components)')
    ax2.set_ylabel('Success Rate', color='b')
    ax2.tick_params(axis='y', labelcolor='b')
    ax2.set_ylim(0, 1)
    ax2.grid(ls='--', alpha=0.3)

    ax2_twin = ax2.twinx()
    ax2_twin.bar(tau_data['tau'], tau_data['count'], alpha=0.3, color='g', label='Test Count')
    ax2_twin.set_ylabel('Number of Tests', color='g')
    ax2_twin.tick_params(axis='y', labelcolor='g')

    # 合并图例
    lines, labels = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2_twin.get_legend_handles_labels()
    ax2.legend(lines + lines2, labels + labels2, loc='best')

    ax2.set_title('Success Rate by τ Value')

    # 图3: 成功率热力图
    ax3 = plt.subplot(2, 2, 3)
    heatmap_data = app_data.groupby(['tau', 'tau_seq'])['val'].mean().reset_index()

    # 如果组合太多，只显示部分
    if len(heatmap_data) > 100:
        heatmap_data = heatmap_data.sample(100, random_state=42)
        title_suffix = " (Sampled 100 Combinations)"
    else:
        title_suffix = ""

    pivot_data = heatmap_data.pivot(index='tau', columns='tau_seq', values='val')

    cmap = LinearSegmentedColormap.from_list('success', ['#d73027', '#fee08b', '#1a9850'])
    sns.heatmap(pivot_data, cmap=cmap, vmin=0, vmax=1,
                cbar_kws={'label': 'Success Rate'}, ax=ax3,
                annot=True, fmt=".2f", linewidths=0.5)
    ax3.set_title(f'Success Rate by τ and Combination{title_suffix}')
    ax3.set_xlabel('Combination Sequence (tau_seq)')
    ax3.set_ylabel('τ (Filled Components)')

    # 图4: 关键性得分分布
    ax4 = plt.subplot(2, 2, 4)
    if criticality_df.empty or len(criticality_df) < 2 or criticality_df['criticality'].nunique() < 2:
        ax4.text(0.5, 0.5, "Insufficient data for distribution", ha='center', va='center')
        ax4.set_title('Criticality Score Distribution')
    else:
        sns.histplot(criticality_df['criticality'], bins=15, kde=True, ax=ax4)
        ax4.axvline(0.7, color='#006837', ls='--', label='Critical')
        ax4.axvline(0.3, color='#369b36', ls=':', label='Enhancing')
        ax4.axvline(-0.3, color='#a50026', ls='--', label='Disruptive')
        ax4.axvline(0, color='k', lw=0.5)
        ax4.set_title('Criticality Score Distribution')
        ax4.set_xlabel('Criticality Score (ψ)')
        ax4.set_ylabel('Frequency')
        ax4.legend()

    # 调整布局并保存
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    safe_app_id = app_id.replace('.', '_').replace(':', '_')
    filename = f"{OUTPUT_DIR}/{safe_app_id}_criticality.png"
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"已生成: {filename}")

print("\n所有页面图表生成完成!")
