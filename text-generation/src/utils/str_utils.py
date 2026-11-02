


class StrUtils:
    SEPARATOR = "::#::"

    @staticmethod
    def parse_component_id(combined_id: str, sep: str = "::#::") -> tuple:
        """安全解析组合ID"""
        if sep not in combined_id:
            return combined_id, None

        parts = combined_id.split(sep, 1)  # 最多分割一次
        if len(parts) != 2:
            raise ValueError(f"无效的组合ID格式: {combined_id}")

        return parts[0], parts[1]
