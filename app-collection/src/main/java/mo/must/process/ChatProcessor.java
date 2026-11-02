package mo.must.process;

import cn.hutool.core.collection.CollectionUtil;
import cn.hutool.core.date.DateUtil;
import cn.hutool.http.HttpRequest;
import cn.hutool.http.HttpResponse;
import com.alibaba.fastjson.JSON;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.commons.lang3.StringUtils;

import java.io.IOException;
import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;


/**
 * @Description: TODO
 * @Author: xiaoche
 * @Date: 2025/5/24 10:38
 */
public class ChatProcessor {
    public static final String XML_PATH = "/Users/xiaoche/king/codeworkspace/mustworkspace/llm-empirical-study-python/output/xml_dumps/";
    public static final String CHAT_URL = "http://127.0.0.1:8682/api/chat/completions";//https://must.lab.help1.com.cn/api/chat/completions

    public static void print(String logFormat, Object... logValue) {
        String format = String.format(logFormat.replace("{}", "%s"), logValue);
        PrintStream printStream = System.out;
        String pf = DateUtil.format(new Date(), "yyyy-MM-dd HH:mm:ss");
        printStream.println("[" + pf + "]" + format);
    }


    public static String dealGPT(String model, String content) {
        Map<String, Object> bobyMap = new HashMap();
        bobyMap.put("modelType", model);
        bobyMap.put("sessionId", "");
        bobyMap.put("message", content);
        String bodyParam = JSON.toJSONString(bobyMap);
        Map<String, String> headers = new HashMap();
        headers.put("Content-Type", "application/json");
        headers.put("Authorization", "Bearer sk-xxx");
        HttpResponse httpResponse = HttpRequest.post(CHAT_URL).body(bodyParam).headerMap(headers, true).execute();
        return httpResponse.body();
    }

    /**
     * 生成完整 Prompt 内容，按结构类型控制拼接部分。
     * promptStructure 含义：
     * 0:global+component+adjacent+restrictive+guiding
     * 1:component+adjacent+restrictive+guiding
     * 2:global+adjacent+restrictive+guiding
     * 3:global+component+restrictive+guiding
     * 4:global+component+adjacent+restrictive
     * 5 global+\nHere is the detailed hierarchy structure of the UI that contains those text-input components:\n xml \n + \n restrictive \n +guiding
     */
    public static String buildPrompt(String appId, GooglePrompts prompts, Integer promptStructure) throws IOException {
        List<String> components = new ArrayList<>();
        // 0:global+component+adjacent+restrictive+guiding
        if (promptStructure == 0) {
            components.add(prompts.getGlobal());
            components.add(prompts.getComponent());
            components.add(prompts.getAdjacent());
            components.add(prompts.getRestrictive());
            components.add(prompts.getGuiding());
        }
        //1:component+adjacent+restrictive+guiding
        if (promptStructure == 1) {
            components.add(prompts.getComponent());
            components.add(prompts.getAdjacent());
            components.add(prompts.getRestrictive());
            components.add(prompts.getGuiding());
        }
        //2:global+adjacent+restrictive+guiding
        if (promptStructure == 2) {
            components.add(prompts.getGlobal());
            components.add(prompts.getAdjacent());
            components.add(prompts.getRestrictive());
            components.add(prompts.getGuiding());
        }
        //3:global+component+restrictive+guiding
        if (promptStructure == 3) {
            components.add(prompts.getGlobal());
            components.add(prompts.getComponent());
            components.add(prompts.getRestrictive());
            components.add(prompts.getGuiding());
        }
        // 4:global+component+adjacent+restrictive
        if (promptStructure == 4) {
            components.add(prompts.getGlobal());
            components.add(prompts.getComponent());
            components.add(prompts.getAdjacent());
            components.add(prompts.getRestrictive());
        }
        // 5 global+\nHere is the detailed hierarchy structure of the UI that contains those text-input components:\n xml \n + \n restrictive \n +guiding
        if (promptStructure == 5) {
            components.add(prompts.getGlobal() + "\n");
            components.add("Here is the detailed hierarchy structure of the UI that contains those text-input components:\n");
            String fileName = "hierarchy_" + appId + ".xml";
            String xmlFileSafely = readXmlFileSafely(fileName);
            if (StringUtils.isBlank(xmlFileSafely)) {
                return null;
            }
            components.add(xmlFileSafely + "\n\n");
            components.add(prompts.getRestrictive() + "\n");
            components.add(prompts.getGuiding());
        }
        return String.join(" ", components);
    }

    public static String readXmlFileSafely(String fileName) throws IOException {
        Path filePath = Paths.get(XML_PATH, fileName);

        if (!Files.exists(filePath)) {
            print("{} 文件不存在", fileName);
            return null;
        }
        return Files.readString(filePath);
    }

    public static List<String> extractComponentIds(String componentIdContent) {
        List<String> keys = new ArrayList<>();
        try {
            // 提取 ```json {...} ``` 中的 {...}
            Pattern pattern = Pattern.compile("```json\\s*(\\{.*?})\\s*```", Pattern.DOTALL);
            Matcher matcher = pattern.matcher(componentIdContent);
            if (matcher.find()) {
                String jsonPart = matcher.group(1);
                // 使用 Jackson 解析 JSON 字符串为 Map
                ObjectMapper mapper = new ObjectMapper();
                Map<String, Object> jsonMap = mapper.readValue(jsonPart, Map.class);

                // 提取所有 key
                keys.addAll(jsonMap.keySet());
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return keys;
    }

    public static boolean validateStructure(List<String> componentIds, Map<String, Object> parsedData) {
        List<String> missing = new ArrayList<>();
        for (String id : componentIds) {
            if (!parsedData.containsKey(id)) {
                missing.add(id);
            }
        }

        if (!missing.isEmpty()) {
            print("Missing components:{}", String.join(", ", missing));
            return false;
        }
        return true;
    }

    /**
     * 解析并验证 LLM 响应
     */
    public static Map<String, Object> parseResponse(List<String> componentIds, Map<String, Object> response) {
        ObjectMapper objectMapper = new ObjectMapper();
        try {
            // 基础验证
            if (!Boolean.TRUE.equals(response.get("success"))) {
                return null;
            }

            Map<String, Object> data = (Map<String, Object>) response.get("data");
            if (data == null) {
                return null;
            }

            String rawContent = (String) data.getOrDefault("chat", "");
            // 检查是否包含 JSON 块
            if (!rawContent.contains("```json")) {
                return null;
            }

            // 提取 JSON 部分
            String[] parts = rawContent.split("```json");
            if (parts.length < 2 || !parts[1].contains("```")) {
                return null;
            }
            String jsonStr = parts[1].split("```")[0].trim();

            // 解析 JSON
            Map<String, Object> parsedData = objectMapper.readValue(jsonStr, Map.class);

            // 验证结构
            if (!validateStructure(componentIds, parsedData)) {
                return null;
            }
            return parsedData;

        } catch (Exception e) {
            print("Parsing error: {}", e.getMessage());
            return null;
        }
    }

    public static Map<String, Object> extractTestInput(String model, List<String> componentIds, String prompt) throws JsonProcessingException {
        ObjectMapper mapper = new ObjectMapper();
        for (int attempt = 1; attempt <= 5; attempt++) {
            String response = dealGPT(model, prompt);
            print("{} 结果:{}", model, response);
            Map<String, Object> responseMap = mapper.readValue(response, Map.class);
            Map<String, Object> parsedResponse = parseResponse(componentIds, responseMap);
            if (parsedResponse == null) {
                print("{} 解析结果不满足条件 (第{}/5 次尝试)", model, attempt);
                continue;
            }
            print("{} 测试文本提取成功 (第 {}/5 次尝试)", model, attempt);
            return parsedResponse;
        }
        return null;
    }


    public static void writeGoogleResultsRetry(GoogleResults results, GoogleResultsMapper resultsMapper, int maxRetries) {
        int attempt = 0;
        boolean success = false;

        while (attempt < maxRetries && !success) {
            try {
                resultsMapper.insertOrUpdate(results);
                print("写入数据库成功: appId={}, model={}, promptStructure={}, seq={}", results.getAppId(), results.getModelType(), results.getPromptStructure(), results.getSeq());
                success = true;
            } catch (Exception e) {
                attempt++;
                print("写入数据库失败（第{}次）: appId={}, model={}, promptStructure={}, seq={}", attempt, results.getAppId(), results.getModelType(), results.getPromptStructure(), results.getSeq());
                e.printStackTrace();
                if (attempt >= maxRetries) {
                    print("超过最大重试次数，放弃写入: appId={}, model={}, promptStructure={}, seq={}", results.getAppId(), results.getModelType(), results.getPromptStructure(), results.getSeq());
                }
            }
        }
    }

    public static void dealGoogleResults(String appId, GoogleResultsMapper resultsMapper, GooglePromptsMapper promptsMapper) {
        try {
            List<GoogleResults> googleResults = resultsMapper.selectByAppId(appId);
            print("检索生成结果数量: {}", (CollectionUtil.isEmpty(googleResults) ? 0 : googleResults.size()));
            Map<String, GoogleResults> googleResultsMap = new HashMap<>();
            if (!CollectionUtil.isEmpty(googleResults)) {
                for (GoogleResults googleResult : googleResults) {
                    String key = googleResult.getModelType() + "_" + googleResult.getPromptStructure() + "_" + googleResult.getSeq();
                    googleResultsMap.put(key, googleResult);
                }
            }

            GooglePrompts googlePrompts = promptsMapper.selectByAppId(appId);
            print("完成 {} 的prompt加载", appId);
            if (googlePrompts == null) {
                print("未找到 {} 对应的 GooglePrompts，请检查数据是否存在", appId);
                return;
            }
            List<String> componentIds = extractComponentIds(googlePrompts.getRestrictive());
            print("提取 {} 的componentId集合:{}", appId, componentIds);
            int seqCount = 3;
            int promptStructureCount = 5;
            List<String> models = buildModels();
            for (String model : models) {
                for (int j = 0; j <= promptStructureCount; j++) {
                    for (int i = 0; i < seqCount; i++) {
                        String key = model + "_" + j + "_" + (i + 1);
                        GoogleResults googleResult = googleResultsMap.get(key);
                        if (googleResult != null) {
                            print("已存在结果: appId={}, model={}, promptStructure={}, seq={}", appId, model, j, (i + 1));
                            continue;
                        }
                        String prompt = buildPrompt(appId, googlePrompts, j);
                        print("构建 {} 的prompt完成, promptStructure={}, prompt={}", appId, j, prompt);
                        if (StringUtils.isBlank(prompt)) {
                            print("构建 {} 的prompt异常, promptStructure={}", appId, j);
                            continue;
                        }
                        Map<String, Object> parsedResponse = extractTestInput(model, componentIds, prompt);
                        print("解析appId={}, model={} 的结果: {}", appId, model, parsedResponse);
                        GoogleResults results = new GoogleResults()
                                .setAppId(appId).setUpdateDate(new Date()).setModelType(model).setSeq((i + 1)).setVal(null).setPromptStructure(j).setTexts(parsedResponse == null ? "{}" : JSON.toJSONString(parsedResponse));
                        writeGoogleResultsRetry(results, resultsMapper, 5);
                    }
                    print("-------------appId={}, model={}, promptStructure={}, {}次解析完成----------------------------", appId, model, j, seqCount);
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }

    }
    public static List<String> buildModels() {
        List<String> models = new ArrayList<>();
        models.add("gpt-4o");
        models.add("Baichuan4");
        models.add("Grok_2");
        models.add("SPARK_4");
        models.add("Deepseek-V1");
        models.add("GLM-4P");
        models.add("CLAUDE_OPUS_4");
        models.add("LLAMA_4_MAVERICK_INSTRUCT");
        return models;
    }

    public static void main(String[] args) {
        try (var session = MyBatisUtil.getSession()) {
            GoogleApkMapper apkMapper = session.getMapper(GoogleApkMapper.class);
            GooglePromptsMapper promptsMapper = session.getMapper(GooglePromptsMapper.class);
            GoogleResultsMapper resultsMapper = session.getMapper(GoogleResultsMapper.class);
            List<String> appIdList = Arrays.asList("com.musescore.playerlite");//apkMapper.selectAppIdList();
            System.out.println(appIdList);
            //"com.kajda.fuelio";
            for (String appId : appIdList) {
                print("加载 APP: {}", appId);
                dealGoogleResults(appId, resultsMapper, promptsMapper);
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

}
