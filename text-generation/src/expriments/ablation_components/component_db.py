import itertools
import json
import logging
import math

import pymysql
from datetime import datetime

db_config = {
    'host': '101.201.82.24',
    'user': 'must_lab_db_user',
    'password': 'REDACTED_USE_ENV_MYSQL_PASSWORD',
    'db': 'must_lab_db',
    'charset': 'utf8mb4'
}


def insert_tao_component_num_records():
    # ===================== 连接数据库 =====================
    source_conn = pymysql.connect(**db_config)
    target_conn = pymysql.connect(**db_config)
    source_cursor = source_conn.cursor()
    target_cursor = target_conn.cursor()

    try:
        # ===================== 步骤1: 查询符合条件的源数据 =====================
        query = """
        SELECT 
            app_id, 
            model_type, 
            seq, 
            val, 
            prompt_structure, 
            texts
        FROM t_google_results
        WHERE prompt_structure IN (0, 5)
        and app_id = 'com.yelp.android.biz'
        """
        source_cursor.execute(query)
        total_source_records = source_cursor.rowcount
        print(f"查询完成，共找到 {total_source_records} 条源记录")

        # ===================== 插入SQL模板 =====================
        insert_query = """
        INSERT INTO t_google_component_results (
            app_id, model_type, seq, val, prompt_structure,
            tao, tao_seq, texts, component_num, update_time
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            texts = VALUES(texts),
            update_time = VALUES(update_time)
        """

        # 计数器
        processed_source = 0
        total_combinations = 0
        skipped_records = 0
        error_records = 0

        # ===================== 步骤2: 处理每条记录的所有组合 =====================
        print("开始处理数据并生成组合...")
        for row in source_cursor:
            processed_source += 1
            app_id, model_type, seq, val, prompt_structure, texts_json = row

            print(f"\n处理源记录 #{processed_source}/{total_source_records}: "
                  f"app_id={app_id}, seq={seq}, prompt_structure={prompt_structure}")

            try:
                # 解析JSON
                texts_dict = {}
                if texts_json and texts_json.strip():
                    try:
                        texts_dict = json.loads(texts_json)
                    except json.JSONDecodeError:
                        raise ValueError("Invalid JSON format")

                # 获取原始组件ID列表
                component_ids = list(texts_dict.keys())
                d = len(component_ids)

                # 当原始组件数为0时，尝试读取默认组件数
                if d == 0:
                    read_d = read_default_num(app_id)
                    if read_d > 0:
                        d = read_d
                        # 创建虚拟组件ID列表 (dummy_1, dummy_2, ...)
                        component_ids = [f'dummy_{i + 1}' for i in range(d)]
                        print(f"  使用默认组件数: d={d}")
                    else:
                        skipped_records += 1
                        print(f"  跳过记录: 组件数{d}且无默认组件数")
                        continue

                print(f"  解析完成: 组件数量 d={d}")

                # 跳过组件数1的记录
                if d == 1:
                    skipped_records += 1
                    print(f"  跳过记录: 组件数{d}≤1")
                    continue

                # 对组件ID排序确保组合顺序一致
                component_ids.sort()
                current_time = datetime.now()

                # 计算总组合数
                total_combos = 2 ** d
                print(f"  将生成 {total_combos} 种组合 (d={d}, 2^{d}={total_combos})")

                # 生成所有可能的 tao 值 (0 到 d)
                for tao in range(0, d + 1):
                    # 计算当前tao的组合数
                    combo_count = math.comb(d, tao)  # 使用math.comb计算组合数

                    print(f"    处理 tao={tao}: 有 {combo_count} 种组合")

                    # 重新生成组合（避免内存问题）
                    combinations = itertools.combinations(component_ids, tao)

                    # 为每个组合创建记录
                    for tao_seq, combo in enumerate(combinations, start=1):
                        # 重构texts：选中的保留原值，未选中的设为""
                        if not texts_dict:  # 原始JSON为空
                            # 为虚拟组件生成占位值：选中组件值为"1"，未选中为""
                            new_texts = {}
                            record_val = 0  # 原始JSON为空时固定为0
                        else:
                            new_texts = {
                                comp_id: texts_dict[comp_id] if comp_id in combo else ""
                                for comp_id in component_ids
                            }
                            # 设置 val：仅当 tao = d 时使用原始值，否则为 NULL
                            record_val = val if tao == d else None

                        # 准备插入数据
                        insert_data = (
                            app_id,
                            model_type,
                            int(seq),
                            record_val,
                            int(prompt_structure),
                            int(tao),
                            int(tao_seq),
                            json.dumps(new_texts),
                            int(d),
                            current_time
                        )

                        # 执行插入
                        target_cursor.execute(insert_query, insert_data)
                        total_combinations += 1

                # 每处理一条源记录后打印状态
                print(f"  完成: 已为当前记录生成 {total_combos} 种组合")

                # 每处理10条源记录提交一次
                if processed_source % 10 == 0:
                    target_conn.commit()
                    print(f"已提交前 {processed_source} 条源记录的组合")

            except json.JSONDecodeError:
                error_records += 1
                print(f"  !! JSON解析失败: app_id={app_id}, seq={seq}, prompt_structure={prompt_structure}")
            except Exception as e:
                error_records += 1
                print(f"  !! 处理失败: app_id={app_id}, seq={seq}, 错误: {str(e)}")

        # 提交剩余数据
        target_conn.commit()
    finally:
        print("\n" + "=" * 50)
        print(f"处理完成!")
        print(f"源记录总数: {total_source_records}")
        print(f"成功处理: {processed_source - skipped_records - error_records} 条")
        print(f"跳过记录: {skipped_records} 条 (组件数≤1或无默认组件)")
        print(f"错误记录: {error_records} 条")
        print(f"生成组合总数: {total_combinations} 条")
        print("=" * 50)


def read_default_num(app_id: str) -> int:
    """
    读取指定page_id的最大组件序列号

    Args:
        app_id: 页面ID

    Returns:
        最大序列号 (若不存在则返回0)
    """
    global conn
    try:
        # ===================== 连接数据库 =====================
        conn = pymysql.connect(**db_config)
        with conn.cursor() as cursor:
            # 使用参数化查询防止SQL注入
            query = """
                SELECT MAX(seq) AS component_number
                FROM t_google_components
                WHERE 
                    page_id = %s
                    AND seq > 1  
                GROUP BY page_id
            """
            cursor.execute(query, (app_id,))
            result = cursor.fetchone()

            # 返回有效结果或默认值
            return result[0] if result else 0

    except Exception as e:
        logging.error(f"读取组件序号失败: {str(e)}")
        return 0
    finally:
        if 'conn' in locals() and conn.open:
            conn.close()


if __name__ == "__main__":
    insert_tao_component_num_records()
