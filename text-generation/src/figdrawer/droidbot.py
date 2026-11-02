import matplotlib.pyplot as plt
import numpy as np
import re
from matplotlib import font_manager

# 设置全局字体为Times New Roman
plt.rcParams['font.family'] = 'serif'
plt.rcParams['font.serif'] = ['Times New Roman']
plt.rcParams['mathtext.fontset'] = 'stix'  # 数学公式字体也设为Times New Roman

# 从文件读取数据
file_path = "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study/src/figdrawer/droidbot.txt"
with open(file_path, 'r') as f:
    data = f.read()

# 解析表格数据
lines = data.strip().split('\n')[3:]  # 跳过标题行
all_data = []

for line in lines:
    # 使用正则表达式匹配数据行
    if re.match(r'^\|\s*\d+', line):
        # 分割并清理数据
        parts = [p.strip() for p in line.split('|')[1:-1]]
        if len(parts) >= 9:
            # 只提取数值数据（跳过文本列）
            nums = []
            for p in parts[2:]:
                try:
                    nums.append(int(p))
                except ValueError:
                    try:
                        nums.append(float(p))
                    except:
                        nums.append(0)
            all_data.append(nums)

# 转换为numpy数组
data_matrix = np.array(all_data[:-1])  # 跳过最后一行平均值

# 提取各组数据
droidbot_dfs_page = data_matrix[:, 0]
droidbotp_dfs_page = data_matrix[:, 2]
droidbot_bfs_page = data_matrix[:, 4]
droidbotp_bfs_page = data_matrix[:, 6]

droidbot_dfs_activity = data_matrix[:, 1]
droidbotp_dfs_activity = data_matrix[:, 3]
droidbot_bfs_activity = data_matrix[:, 5]
droidbotp_bfs_activity = data_matrix[:, 7]

# 计算均值
mean_dfs_page_droidbot = np.mean(droidbot_dfs_page)
mean_dfs_page_droidbotp = np.mean(droidbotp_dfs_page)
mean_bfs_page_droidbot = np.mean(droidbot_bfs_page)
mean_bfs_page_droidbotp = np.mean(droidbotp_bfs_page)

mean_dfs_activity_droidbot = np.mean(droidbot_dfs_activity)
mean_dfs_activity_droidbotp = np.mean(droidbotp_dfs_activity)
mean_bfs_activity_droidbot = np.mean(droidbot_bfs_activity)
mean_bfs_activity_droidbotp = np.mean(droidbotp_bfs_activity)

# 计算提升比例
page_improvement_dfs = (mean_dfs_page_droidbotp - mean_dfs_page_droidbot) / mean_dfs_page_droidbot * 100
page_improvement_bfs = (mean_bfs_page_droidbotp - mean_bfs_page_droidbot) / mean_bfs_page_droidbot * 100
activity_improvement_dfs = (mean_dfs_activity_droidbotp - mean_dfs_activity_droidbot) / mean_dfs_activity_droidbot * 100
activity_improvement_bfs = (mean_bfs_activity_droidbotp - mean_bfs_activity_droidbot) / mean_bfs_activity_droidbot * 100

# 创建1行2列的图像
fig, axes = plt.subplots(1, 2, figsize=(6, 3))

space = "  "
edge = -23
# 设置全局样式
plt.rcParams.update({
    'font.size': 12,
    'axes.titlesize': 14,
    'axes.labelsize': 12,
    'xtick.labelsize': 11,
    'ytick.labelsize': 11
})

# 颜色设置
colors = ['#1f77b4', '#ff7f0e']  # 蓝色: DroidBot, 橙色: DroidBot+

# ================== DFS策略 ==================
ax1 = axes[0]
ax1.set_title('DFS Strategy', fontname='Times New Roman', fontsize=10)

# 创建左侧Y轴（Number of Explored UI Pages）
ax1.set_ylabel('Number of Explored UI Pages', fontname='Times New Roman', fontsize=10)
# ax1.grid(axis='y', linestyle='--', alpha=0.7, zorder=1)
ax1.set_ylim(0, 130)  # 设置统一的Y轴范围

# 创建右侧Y轴（Number of Explored Activities）
ax2 = ax1.twinx()
ax2.set_ylim(0, 130)  # 设置统一的Y轴范围
# 隐藏右侧Y轴的标签和刻度文字
ax2.set_yticklabels([])
ax2.set_ylabel('')

# 设置位置参数 - 调整间距
positions_page = [1, 2]  # DroidBot和DroidBot+在Page的位置
positions_activity = [3, 4]  # DroidBot和DroidBot+在Activity的位置

# 绘制Number of Explored UI Pages箱线图
box_page = ax1.boxplot(
    [droidbot_dfs_page, droidbotp_dfs_page],
    positions=positions_page,
    widths=0.15,  # 更窄的柱子
    patch_artist=True,
    showmeans=True,
    flierprops=dict(marker='o', markersize=3, markerfacecolor='none', markeredgecolor='black'),
    meanprops={'marker': 'D', 'markerfacecolor': 'red', 'markeredgecolor': 'black', 'markersize': 3},
    zorder=3  # 确保箱线图在网格之上
)

# 绘制Number of Explored Activities箱线图
box_activity = ax2.boxplot(
    [droidbot_dfs_activity, droidbotp_dfs_activity],
    positions=positions_activity,
    widths=0.15,  # 更窄的柱子
    patch_artist=True,
    showmeans=True,
    flierprops=dict(marker='o', markersize=3, markerfacecolor='none', markeredgecolor='black'),
    meanprops={'marker': 'D', 'markerfacecolor': 'red', 'markeredgecolor': 'black', 'markersize': 3},
    zorder=3  # 确保箱线图在网格之上

)

# 设置颜色
for patch, color in zip(box_page['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

for patch, color in zip(box_activity['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

# 设置x轴刻度和标签
ax1.set_xticks([1.5, 3.5])  # 设置标签在两组中间
ax1.set_xticklabels(['UI Pages', 'Activities'], fontname='Times New Roman', fontsize=10)

# 添加分隔线
ax1.axvline(x=2.5, color='grey', linestyle='--', linewidth=0.5)

# 添加Page均值标记
ax1.text(1, mean_dfs_page_droidbot, f'{space}{mean_dfs_page_droidbot:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')
ax1.text(2, mean_dfs_page_droidbotp, f'{space}{mean_dfs_page_droidbotp:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')

# 添加Activity均值标记
ax2.text(3, mean_dfs_activity_droidbot, f'{space}{mean_dfs_activity_droidbot:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')
ax2.text(4, mean_dfs_activity_droidbotp, f'{space}{mean_dfs_activity_droidbotp:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')

# 添加提升比例 - 带边框
ax1.text(1.5, edge,
         f'$\\blacktriangle$ ({page_improvement_dfs:.2f}%)',
         ha='center', va='bottom', fontsize=10, fontweight='bold', color='green',
         fontname='Times New Roman')
ax2.text(3.5, edge,
         f'$\\blacktriangle$ ({activity_improvement_dfs:.2f}%)',
         ha='center', va='bottom', fontsize=10, fontweight='bold', color='green',
         fontname='Times New Roman')

# ================== BFS策略 ==================
ax1 = axes[1]
ax1.set_title('BFS Strategy', fontname='Times New Roman', fontsize=10)

# 创建左侧Y轴（Number of Explored UI Pages）
ax1.set_ylim(0, 130)  # 设置统一的Y轴范围
# 隐藏左侧Y轴的标签和刻度文字
ax1.set_yticklabels([])
ax1.set_ylabel('')

# ax1.grid(axis='y', linestyle='--', alpha=0.7)

# 创建右侧Y轴（Number of Explored Activities）
ax2 = ax1.twinx()
ax2.set_ylim(0, 130)  # 设置统一的Y轴范围
ax2.set_ylabel('Number of Explored Activities', fontname='Times New Roman', fontsize=10)

# 绘制Number of Explored UI Pages箱线图
box_page = ax1.boxplot(
    [droidbot_bfs_page, droidbotp_bfs_page],
    positions=positions_page,
    widths=0.15,  # 更窄的柱子
    patch_artist=True,
    showmeans=True,
    flierprops=dict(marker='o', markersize=3, markerfacecolor='none', markeredgecolor='black'),
    meanprops={'marker': 'D', 'markerfacecolor': 'red', 'markeredgecolor': 'black', 'markersize': 3}
)

# 绘制Number of Explored Activities箱线图
box_activity = ax2.boxplot(
    [droidbot_bfs_activity, droidbotp_bfs_activity],
    positions=positions_activity,
    widths=0.15,  # 更窄的柱子
    patch_artist=True,
    showmeans=True,
    flierprops=dict(marker='o', markersize=3, markerfacecolor='none', markeredgecolor='black'),
    meanprops={'marker': 'D', 'markerfacecolor': 'red', 'markeredgecolor': 'black', 'markersize': 3}
)

# 设置颜色
for patch, color in zip(box_page['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

for patch, color in zip(box_activity['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)
    meanprops = {'marker': 'D', 'markerfacecolor': 'white', 'markeredgecolor': 'black', 'markersize': 3}

# 设置x轴刻度和标签
ax1.set_xticks([1.5, 3.5])  # 设置标签在两组中间
ax1.set_xticklabels(['UI Pages', 'Activities'], fontname='Times New Roman', fontsize=10)

# 添加分隔线
ax1.axvline(x=2.5, color='grey', linestyle='--', linewidth=0.5)

# 添加Page均值标记
ax1.text(1, mean_bfs_page_droidbot, f'{space}{mean_bfs_page_droidbot:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')
ax1.text(2, mean_bfs_page_droidbotp, f'{space}{mean_bfs_page_droidbotp:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')

# 添加Activity均值标记
ax2.text(3, mean_bfs_activity_droidbot, f'{space}{mean_bfs_activity_droidbot:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')
ax2.text(4, mean_bfs_activity_droidbotp, f'{space}{mean_bfs_activity_droidbotp:.2f}',
         ha='left', va='center', fontsize=8, fontname='Times New Roman', color='red', fontweight='bold')

# 添加提升比例 - 带边框
ax1.text(1.5, edge,
         f'$\\blacktriangle$ ({page_improvement_bfs:.2f}%)',
         ha='center', va='bottom', fontsize=10, fontweight='bold', color='green',
         fontname='Times New Roman',
         math_fontfamily='stix')
ax2.text(3.5, edge,
         f'$\\blacktriangle$ ({activity_improvement_bfs:.2f}%)',
         ha='center', va='bottom', fontsize=10, fontweight='bold', color='green',
         fontname='Times New Roman')

# 添加图例（整个图形共用）
from matplotlib.patches import Patch

legend_elements = [
    Patch(facecolor='#1f77b4', edgecolor='black', alpha=0.7, label='DroidBot'),
    Patch(facecolor='#ff7f0e', edgecolor='black', alpha=0.7, label='DroidBot with LLM-generated text inputs')
]
fig.legend(handles=legend_elements, loc='lower center',
           bbox_to_anchor=(0.5, -0.08), ncol=2, prop={'family': 'Times New Roman', 'size': 10}, edgecolor='white',
           frameon=False)

# 调整布局，为底部图例留出空间
plt.tight_layout()
plt.subplots_adjust(bottom=0.15)  # 增加底部空间

# 保存为PNG
plt.savefig('result_4.1_droidbot_improvement_comparison.pdf', dpi=300, bbox_inches='tight')
print("图像已保存为 'combined_coverage_comparison.png'")
