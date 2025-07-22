# src/llm_integration/text_input_extractor.py
import json
import logging
from pathlib import Path
from typing import Dict, Tuple

from src.utils.yaml_utils import YamlUtils

logger = logging.getLogger(__name__)


class TextInputExtractor:
    def __init__(self, llm_chatter, max_retries, context_data):
        self.llm_chatter = llm_chatter
        self.max_retries = max_retries
        self.component_ids = [c["resource_id_combined"] for c in context_data["component"]]
        self.package_name = context_data['global']['package_name']
        self.retry_templates = YamlUtils.load_prompt_config().get('Retry')

    def _generate_example_json(self) -> str:
        """构建限制性提示（动态生成JSON示例）"""
        example_dict = {rid: "generated_value" for rid in self.component_ids}
        example_json = json.dumps(example_dict, indent=2)

        return example_json

    def _build_retry_prompt(self) -> str:
        """构建重试提示"""
        return self.retry_templates.format(
            example_json=self._generate_example_json(),
            component_count=len(self.component_ids)
        )

    def _validate_structure(self, parsed_data: Dict) -> bool:
        """验证响应数据结构"""
        # 检查所有必需组件是否存在
        missing = [rid for rid in self.component_ids if rid not in parsed_data]
        if missing:
            logger.warning(f" Missing components: {', '.join(missing)}")
            return False
        return True

    def _parse_response(self, response: Dict) -> Tuple[str, Dict]:
        """解析并验证LLM响应"""
        try:
            # 基础验证
            if not response.get('success', False):
                return "Failed--", {}

            data = response['data']
            raw_content = data.get('chat', '')
            session_id = data.get('id', '')

            # JSON解析
            if "```json" not in raw_content:
                return str(session_id), {}

            # 分割字符串提取JSON部分
            json_str = raw_content.split("```json")[1].split("```")[0].strip()
            parsed_data = json.loads(json_str)

            # 结构验证
            if not self._validate_structure(parsed_data):
                return str(session_id), {}

            return str(session_id), parsed_data

        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Parsing error: {str(e)}")
            return "", {}

    def extract_test_input(self, initial_response: Dict, prompt: str) -> Tuple[str, Dict]:

        llm_response = initial_response  # 当前LLM响应

        for attempt in range(1, self.max_retries):

            session_id, parsed_data = self._parse_response(llm_response)

            if session_id == "Failed--":
                logger.warning(f"\t🔌 连接失败 (第 {attempt + 1}/{self.max_retries} 次尝试)")
                current_prompt = prompt  # 重置为初始提示
                session_id = ""  # 重置会话

            elif not parsed_data:
                logger.debug(f"\t⚠️ 解析结果不满足条件 (第 {attempt + 1}/{self.max_retries} 次尝试)")
                current_prompt = self._build_retry_prompt()  # 切换重试提示

            else:
                logger.info(f"✅ 测试文本提取成功")
                return session_id, parsed_data

            if attempt < self.max_retries:
                llm_response = self.llm_chatter.chat_completion(
                    message=current_prompt,
                    session_id=session_id
                )

        logger.error(f"🚫 达到最大尝试次数 {self.max_retries} 次，默认返回空")
        return "TAG：次数用完，未成功提取测试用例", {}


if __name__ == "__main__":
    def save_output(data: Dict):
        """保存数据到文件"""
        try:
            save_dir = Path(
                '/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study/output/generated_texts')
            save_dir.mkdir(parents=True, exist_ok=True)
            filename = save_dir / f"testingtttttt.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info(f"Saved {len(data)} entries to {filename}")

        except IOError as e:
            logger.error(f"File save failed: {str(e)}")
            raise


    raw_content = '''To generate a valid text input for the EditText component with the ID `com.peerspace.app:id/phoneNumberInput`, we need to consider the context and requirements provided by the associated labels and hints.
                
                1. **Analyze the Hint Text**: The hint text "Phone Number" suggests that the input should be a phone number. This indicates that the input should follow standard phone number formatting.
                
                2. **Consider the Left Adjacent Label**: The left adjacent label is "+1", which is the international dialing code for the United States and Canada. This suggests that the phone number should be a valid US or Canadian phone number.
                
                3. **Examine the Bottom Adjacent Label**: The bottom adjacent label mentions "We’ll text you a code to verify your number," which emphasizes that the input must be a valid, textable phone number. Additionally, it warns about "Standard message and data rates may apply," indicating functionality for messaging and that the number should adhere to typical cell phone number formats.
                
                4. **Determine the Formatting**: A valid US phone number typically consists of a ten-digit number following the country code, normally partitioned as (XXX) XXX-XXXX or simply as XXXXXXXXXX for straightforward input.
                
                5. **Strategy for Generating Input**: Based on these observations, we'll generate a ten-digit phone number that follows the common format, ensuring it's plausible within the +1 dialing region. A commonly recognized format without any separators is chosen for input simplicity.
                
                Taking all these considerations into account, here's an example of a valid generated input:
                
                ```json
                {
                  "com.peerspace.app:id/phoneNumberInput": "1234567890",
                  "com.peerspace.app:id/phoneNumberInput1": "+1 (555) 123-4567",
                  "com.peerspace.app:id/phoneNumberInput2": "+1 (555) 123-4567"
                }
                ```
                
                ### Explanation:
                - *"1234567890"* is a straightforward, valid ten-digit number that forms a plausible phone number when used in context with the "+1" country code.
                - It doesn’t include additional formatting characters like parentheses or dashes, aligning with systems that may not recognize them during verification or input.
                - The sequence is simple yet adheres to the expectation of being a ten-digit figure after the "+1" prefix, fulfilling the requirements for a valid phone input as derived from the contextual labels and hints.'''
    try:
        # 提取JSON代码块
        if "```json" not in raw_content:
            raise ValueError("Invalid response content")

        # 分割字符串提取JSON部分
        json_str = raw_content.split("```json")[1].split("```")[0].strip()
        parsed_data = json.loads(json_str)
        save_output(parsed_data)
        print(parsed_data)


    except json.JSONDecodeError as e:
        print(f"JSON解析失败: {str(e)}")
    except IndexError:
        print("代码块标记不完整")
