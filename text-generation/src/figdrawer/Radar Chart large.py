import mysql.connector
import re

# 数据库配置
db_config = {
    'host': '101.201.82.24',  # 数据库主机地址
    'user': 'must_lab_db_user',  # 数据库用户名
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',  # 数据库密码
    'database': 'must_lab_db'  # 数据库名称
}

# 需要特殊标记的APP ID列表
STAR_APPS = {
    'com.BabyName.start',
    'com.applabstudios.ai.mail.homescreen.inbox',
    'com.colt',
    'com.deepstash',
    'com.economist.lamarr',
    'com.frameme.photoeditor.collagemaker.effects',
    'com.golftripgenius.android.leaguegenius',
    'com.hoopladigital.android',
    'com.kw.literie',
    'com.limebike',
    'com.musescore.playerlite',
    'com.opentable',
    'com.scribd.app.reader0',
    'com.teamreach.app',
    'com.tickpickllc.ceobrien.tickpick',
    'com.trubeacon.scooters_mobile_android',
    'com.ultimateguitar.tabs',
    'com.wizards.winter_orb',
    'com.yelp.android.biz',
    'org.wikipedia'
}


def convert_install_num(install_str):
    """
    将安装数字符串转换为简洁格式
    """
    if not install_str:
        return install_str

    # 提取数字部分（移除逗号和末尾的加号）
    clean_str = install_str.replace(',', '').rstrip('+')

    try:
        num = int(clean_str)
    except (ValueError, TypeError):
        return install_str  # 转换失败时返回原始字符串

    # 按量级转换
    if num >= 1_000_000_000:
        value = num / 1_000_000_000
        return f"{format_value(value)}B+"
    elif num >= 1_000_000:
        value = num / 1_000_000
        return f"{format_value(value)}M+"
    elif num >= 1_000:
        value = num / 1_000
        return f"{format_value(value)}K+"
    else:
        return f"{num}+"  # 小于1000的直接返回


def format_value(value):
    """格式化数值，移除冗余的小数部分"""
    if value.is_integer():
        return str(int(value))
    else:
        # 保留1位小数，并移除可能的.0
        formatted = f"{value:.1f}"
        return formatted.rstrip('0').rstrip('.')  # 移除尾随零和点


def process_app_data():
    """从数据库获取数据并处理"""
    try:
        # 连接数据库
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 执行查询：获取符合条件的app数据
        query = """
        SELECT a.app_id, a.install_num
        FROM t_google_apks a
        WHERE EXISTS (
            SELECT 1 FROM t_google_results r 
            WHERE r.app_id = a.app_id
        )
        """
        cursor.execute(query)
        results = cursor.fetchall()

        # 处理数据
        processed_data = []
        for row in results:
            # 处理app_id：添加特殊标记
            app_id = row['app_id']
            if app_id in STAR_APPS:
                app_id = f"{app_id} ($\\bigstar$)"

            # 处理install_num：转换为简洁格式
            install_num = row['install_num']
            formatted_install = convert_install_num(install_num)

            processed_data.append({
                'app_id': app_id,
                'install_num': formatted_install
            })

        return processed_data

    except mysql.connector.Error as err:
        print(f"数据库错误: {err}")
        return []
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()


# 执行处理并输出结果
if __name__ == "__main__":
    data = process_app_data()
    print("处理结果:")
    for item in data:  # 打印前10条结果
        print(f"{item['app_id']} ; {item['install_num']}")

    print(f"\n共处理 {len(data)} 条记录")