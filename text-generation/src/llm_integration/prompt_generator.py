import json
from pathlib import Path
from typing import Dict, List

from src.utils.db_utils import DBUtils
from src.utils.logger import get_logger
from src.utils.yaml_utils import YamlUtils

logger = get_logger(__name__)


class PromptEngine:
    """模块化提示生成引擎，支持子提示存储"""

    def __init__(self):
        self.templates = YamlUtils.load_prompt_config()
        self.sub_prompts = {}  # 存储各子提示内容

    def _get_ordinal_suffix(self, n: int) -> str:
        """生成序数词后缀 (1st, 2nd, 3rd...)"""
        if 11 <= (n % 100) <= 13:
            return "th"
        return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

    def _format_ordinal(self, number: int) -> str:
        """生成完整序数词"""
        return f"{number}{self._get_ordinal_suffix(number)}"

    def _record_subprompt(self, key: str, content: str or list):
        """记录子提示内容"""
        if isinstance(content, list):
            self.sub_prompts.setdefault(key, []).extend(content)
        else:
            self.sub_prompts[key] = content

    def build_prompt(self, context: Dict) -> str:
        """构建完整提示并保存子提示"""
        self.sub_prompts.clear()
        components = []

        # 全局上下文
        glop = self._build_global(context)
        components.append(glop)
        self._record_subprompt("GloP", glop)

        # 组件描述
        comps = self._build_components(context)
        components.extend(comps)
        self._record_subprompt("ComP", comps)

        # 相邻上下文
        adjps = self._build_adjacent(context)
        components.extend(adjps)
        self._record_subprompt("AdjP", adjps)

        # 限制性提示
        resp = self._build_restrictive(context)
        components.append(resp)
        self._record_subprompt("ResP", resp)

        # 指导性提示
        guip = self.templates["GuiP"]
        components.append(guip)
        self._record_subprompt("GuiP", guip)

        # 添加完整提示内容
        full_prompt = " ".join(components)
        self.sub_prompts["FullPrompt"] = full_prompt

        # 保存子提示
        DBUtils.save_prompt(context["global"]["package_name"], glop, str(comps), str(adjps), resp, guip)
        logger.info(f"✅ prompt生成成功")

        return full_prompt

    def _build_global(self, context: Dict) -> str:
        """构建全局上下文提示"""
        global_info = context["global"]
        component_types = {c["type"].split('.')[-1] for c in context["component"]}
        return self.templates["GloP"].format(
            app_name=global_info["app_name"],
            input_count=global_info["input_count"],
            activity=global_info["activity"].split('.')[-1],
            component_types=", ".join(component_types)
        )

    def _build_components(self, context: Dict) -> List[str]:
        """构建组件描述提示"""
        components = []
        for idx, comp in enumerate(context["component"], 1):
            components.append(
                self.templates["ComP"].format(
                    component_order=self._format_ordinal(idx),
                    component_type=comp["type"].split('.')[-1],
                    resource_id=comp["resource_id"],
                    hint_text=comp.get("hint", ""),
                    current_text=comp.get("text", "")
                )
            )
        return components

    def _build_adjacent(self, context: Dict) -> List[str]:
        """构建相邻上下文提示"""
        adjacents = []
        for comp_id, adj_info in context["adjacent"].items():
            for direction, info in adj_info.items():
                if info and info.get("text"):
                    adjacents.append(
                        self.templates["AdjP"].format(
                            direction=direction,
                            component_id=comp_id,
                            text=info["text"],
                            distance=f"{info.get('distance', 0):.2f}"
                        )
                    )
        return adjacents

    def _build_restrictive(self, context: Dict) -> str:
        """构建限制性提示（动态生成JSON示例）"""
        resource_ids = [c["resource_id_combined"] for c in context["component"]]
        example_dict = {rid: "generated_value" for rid in resource_ids}
        example_json = json.dumps(example_dict, indent=2)

        return self.templates["ResP"].format(
            component_list=", ".join(resource_ids),
            example_json=example_json
        )

    def _save_sub_prompts(self, save_dir: Path, package_name: str):
        """保存子提示到JSON文件"""
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = save_dir / f"prompts_{package_name}.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.sub_prompts, f, indent=2, ensure_ascii=False)
