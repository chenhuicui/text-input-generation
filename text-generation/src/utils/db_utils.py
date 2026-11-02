import logging

import pandas as pd

from src.utils.yaml_utils import YamlUtils

logger = logging.getLogger(__name__)

from mysql.connector import pooling
import json


class DBUtils:
    _connection_pool = None

    @classmethod
    def _initialize_pool(cls):
        """从YAML文件读取配置并初始化连接池"""
        if cls._connection_pool is None:
            try:
                db_config = YamlUtils.load_db_config()

                # 创建连接池
                cls._connection_pool = pooling.MySQLConnectionPool(
                    pool_name=db_config.get('pool_name', 'mypool'),
                    pool_size=db_config.get('pool_size', 5),
                    host=db_config['host'],
                    port=db_config['port'],
                    user=db_config['user'],
                    password=db_config['password'],
                    database=db_config['database']
                )
            except Exception as e:
                raise RuntimeError(f"Failed to initialize database pool: {e}")

    @classmethod
    def save_result_value(cls, app_id: str, model_type: str, seq: int, val: int, prompt_structure: int, texts: dict):
        """
        插入或更新记录（根据 appid + model_type + seq 判断是否存在）

        :param app_id: 应用ID
        :param model_type: 模型类型
        :param seq: 序列号
        :param val: 值
        :param prompt_structure: 提示码
        :param texts: JSON文本数据
        """
        cls._initialize_pool()  # 确保连接池已初始化

        # SQL语句（使用ON DUPLICATE KEY UPDATE实现插入/更新）
        query = """
        INSERT INTO t_google_results
            (app_id, model_type, seq, val, prompt_structure, texts, update_time)
        VALUES 
            (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            val = VALUES(val),
            texts = VALUES(texts),
            update_time = NOW()
        """

        # 参数化查询的变量
        params = (
            app_id,
            model_type,
            seq,
            val,
            prompt_structure,
            json.dumps(texts)  # 将字典转为JSON字符串
        )

        # 从连接池获取连接
        try:
            with cls._connection_pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Database operation failed: {e}")
        finally:
            if 'cursor' in locals():
                cursor.close()

    @classmethod
    def save_prompt(cls, app_id: str, global_prompt: str, component_prompt: str, adjacent_prompt: str,
                    restrictive_prompt: str, guiding_prompt: str):
        cls._initialize_pool()

        query = """
        INSERT INTO t_google_prompts
            (app_id, global, component, adjacent, restrictive, guiding, update_time)
        VALUES 
            (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            global = VALUES(global),
            component = VALUES(component),
            adjacent = VALUES(adjacent),
            restrictive = VALUES(restrictive),
            guiding = VALUES(guiding),
            update_time = NOW()
        """

        params = (
            app_id,
            global_prompt,
            component_prompt,
            adjacent_prompt,
            restrictive_prompt,
            guiding_prompt
        )

        # 从连接池获取连接
        try:
            with cls._connection_pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Database operation failed: {e}")
        finally:
            if 'cursor' in locals():
                cursor.close()

    @classmethod
    def load_data(cls, FIXED_MODEL: str, FIXED_PROMPT: int):
        # 连接到SQLite数据库（根据您的实际数据库类型调整）
        cls._initialize_pool()
        # 执行查询 - 获取所需数据
        query = f"""
        SELECT 
            app_id,
            model_type,
            seq,
            val,
            prompt_structure,
            tau,
            tau_seq,
            component_num,
            component,
            combination  -- 确保表中有此字段
        FROM t_google_component_results
        WHERE model_type = '{FIXED_MODEL}' 
            AND prompt_structure = {FIXED_PROMPT}
        """

        df = pd.read_sql_query(query,         cls._initialize_pool() )

        # 数据预处理
        if 'combination' not in df.columns:
            # 如果表中没有combination字段，根据其他字段创建
            print("警告: 表中无combination字段，将根据tau_seq和component_num模拟生成")
            df['combination'] = df.apply(lambda x: f"Combination_{x['tau']}_{x['tau_seq']}", axis=1)

        return df
