#!/usr/bin/env python3
"""测试 LLM API 连通性（读取 config.yaml 配置）。"""

from nervos_brain.graph_engine.llm import get_api_key, get_api_base, get_model_name, call_llm, call_llm_json

print(f"模型: {get_model_name()}")
print(f"API Base: {get_api_base()}")
print(f"API Key: {get_api_key()[:8]}..." if get_api_key() else "API Key: 未配置")

print("\n测试 1: 普通文本补全")
try:
    result = call_llm("你是一个测试助手", "回复'OK'两个字母", max_tokens=32)
    print(f"  结果: {result}")
    print("  ✅ 成功")
except Exception as e:
    print(f"  ❌ 失败: {e}")

print("\n测试 2: JSON mode 补全")
try:
    result = call_llm_json("你是一个JSON生成器，只输出合法JSON", '输出 {"status": "ok"}')
    print(f"  结果: {result}")
    print("  ✅ 成功")
except Exception as e:
    print(f"  ❌ 失败: {e}")

print("\n完成!")
