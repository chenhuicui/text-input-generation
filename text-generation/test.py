import os
import shutil

# 定义目标字段列表
target_fields = [
    "com.BabyName.start",
    "com.applabstudios.ai.mail.homescreen.inbox",
    "com.colt",
    "com.deepstash",
    "com.economist.lamarr",
    "com.frameme.photoeditor.collagemaker.effects",
    "com.golftripgenius.android.leaguegenius",
    "com.hoopladigital.android",
    "com.kw.literie",
    "com.limebike",
    "com.musescore.playerlite",
    "com.opentable",
    "com.scribd.app.reader0",
    "com.teamreach.app",
    "com.tickpickllc.ceobrien.tickpick",
    "com.trubeacon.scooters_mobile_android",
    "com.ultimateguitar.tabs",
    "com.wizards.winter_orb",
    "com.yelp.android.biz",
    "org.wikipedia"
]

# 配置路径
source_dir = "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study/output/screenshots"  # 替换为你的图片文件夹路径
dest_dir = "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study/output/screenshots/comparison"  # 新文件夹名

# 支持的图片扩展名
image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff')

# 创建目标文件夹
os.makedirs(dest_dir, exist_ok=True)

# 计数器
found_count = 0

# 遍历源文件夹
for filename in os.listdir(source_dir):
    # 检查是否已找到20个
    if found_count >= 20:
        break

    # 检查文件扩展名
    if filename.lower().endswith(image_exts):
        # 检查是否包含任意目标字段
        if any(field in filename for field in target_fields):
            # 构建完整路径
            src_path = os.path.join(source_dir, filename)
            dest_path = os.path.join(dest_dir, filename)

            # 复制文件
            shutil.copy2(src_path, dest_path)
            print(f"已复制: {filename}")
            found_count += 1

print(f"\n操作完成！共复制 {found_count} 个文件到 {dest_dir}/")