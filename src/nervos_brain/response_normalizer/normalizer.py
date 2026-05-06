# ============================================================
# normalizer.py —— M3-T7/T8/T9/T10 ResponseNormalizer 雏形
# ============================================================
# 这个文件是"出门前的整理员"。
#
# 模型（或 Mock 节点）生成的回答不能直接发给用户，
# 因为可能存在以下问题：
#   1. 缺少必填字段
#   2. 引用编号和 citations 列表不对应
#   3. Markdown 代码块没闭合
#   4. 文本太长，超过平台的单条消息字符限制
#
# 所以回答必须经过一条"整理流水线"：
#   validate_response_shape()  → 检查格式
#   normalize_citations()      → 修复引用编号
#   sanitize_markdown()        → 修复 Markdown
#   chunk_for_platform()       → 按平台限制切段
#
# M3 阶段这些函数都是最小雏形，只处理最常见的情况。
# 后续 Milestone 7 会逐步完善。
# ============================================================

import re
from typing import Any, Dict, List, Tuple


# ============================================================
# M3-T7: validate_response_shape() —— 最小输出类型检查
# ============================================================

def validate_response_shape(response: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    检查一个字典是否具备 AssistantResponse 的最低要求。

    参数：
        response: 要检查的字典

    返回：
        (是否通过, 错误消息列表)
        例如 (True, []) 或 (False, ["缺少必填字段: 'text'"])

    为什么单独写一个函数而不直接用 validators.py 里的？
        因为 validators.py 是通用校验，这里的函数是专门给
        ResponseNormalizer 流水线用的，后续可能会加入
        流水线特有的检查逻辑（比如自动修复而不是只报错）。
    """
    errors: List[str] = []

    # 检查必填字段
    for key in ("request_id", "text", "citations"):
        if key not in response:
            errors.append(f"缺少必填字段: '{key}'")

    if errors:
        return False, errors

    # 检查 citations 是否是列表
    if not isinstance(response.get("citations"), list):
        errors.append("'citations' 必须是一个列表")

    # 检查 text 是否是字符串
    if not isinstance(response.get("text"), str):
        errors.append("'text' 必须是一个字符串")

    is_valid = len(errors) == 0
    return is_valid, errors


# ============================================================
# M3-T8: normalize_citations() —— 引用编号修复
# ============================================================

def normalize_citations(
    text: str,
    citations: List[Dict[str, str]],
) -> Tuple[str, List[Dict[str, str]]]:
    """
    修复引用编号，确保 text 里的 [1]、[2] 和 citations 列表一一对应。

    处理逻辑：
      1. 从 text 中提取所有引用编号（[1]、[2] 等）
      2. 按出现顺序重新编号
      3. 删除 text 中存在但 citations 里没有的孤儿引用
      4. 删除 citations 里存在但 text 中没用到的多余条目

    参数：
        text: 回答正文
        citations: 引用列表

    返回：
        (修复后的正文, 修复后的引用列表)
    """
    if not citations:
        # 没有引用，直接把正文里的引用标记都删掉
        cleaned_text = re.sub(r"\[\d+\]", "", text).strip()
        return cleaned_text, []

    # 第 1 步：建一个 label → citation 的映射
    label_to_citation: Dict[str, Dict[str, str]] = {}
    for cit in citations:
        label = cit.get("label", "")
        if label:
            label_to_citation[label] = cit

    # 第 2 步：从 text 中按出现顺序提取所有引用编号
    found_labels_in_order: List[str] = []
    seen: set = set()
    for match in re.finditer(r"\[\d+\]", text):
        label = match.group()
        if label not in seen:
            found_labels_in_order.append(label)
            seen.add(label)

    # 第 3 步：只保留 text 中出现且 citations 里也有的引用
    valid_labels = [
        label for label in found_labels_in_order
        if label in label_to_citation
    ]

    # 第 4 步：重新编号
    new_text = text
    new_citations: List[Dict[str, str]] = []
    old_to_new: Dict[str, str] = {}

    for new_index, old_label in enumerate(valid_labels, start=1):
        new_label = f"[{new_index}]"
        old_to_new[old_label] = new_label

        # 复制 citation 并更新 label
        old_cit = label_to_citation[old_label]
        new_cit = dict(old_cit)
        new_cit["label"] = new_label
        new_citations.append(new_cit)

    # 第 5 步：替换 text 中的旧编号为新编号
    # 为了避免替换冲突（[1]->[2] 再 [2]->[1]），先用占位符
    for old_label, new_label in old_to_new.items():
        placeholder = f"__CITE_{new_label}__"
        new_text = new_text.replace(old_label, placeholder)

    for new_label in old_to_new.values():
        placeholder = f"__CITE_{new_label}__"
        new_text = new_text.replace(placeholder, new_label)

    # 第 6 步：删掉 text 中没有对应 citation 的孤儿引用
    orphan_labels = seen - set(old_to_new.keys())
    for orphan in orphan_labels:
        new_text = new_text.replace(orphan, "")

    return new_text.strip(), new_citations


# ============================================================
# M3-T9: sanitize_markdown() —— Markdown 修复
# ============================================================

def sanitize_markdown(text: str) -> str:
    """
    修复常见的 Markdown 格式问题。

    目前处理的情况（M3 最小集合）：
      1. 自动闭合未闭合的 ``` 代码块
      2. 清理 <think>...</think> 推理标签
      3. 清理多余的空行（超过 2 个连续空行压缩成 2 个）

    参数：
        text: 原始 Markdown 文本

    返回：
        修复后的 Markdown 文本
    """
    # 规则 1：清理 <think>...</think> 推理标签
    # 有些模型会在输出里包含推理过程，不应该展示给用户
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL,  # 让 . 也匹配换行符
    )

    # 规则 2：自动闭合未闭合的 ``` 代码块
    # 数一下 ``` 出现了多少次，如果是奇数说明没闭合
    backtick_count = text.count("```")
    if backtick_count % 2 != 0:
        # 奇数个 ```，说明最后一个代码块没闭合，补一个
        text = text.rstrip() + "\n```"

    # 规则 3：压缩多余空行（超过 2 个连续空行 → 2 个）
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ============================================================
# M3-T10: chunk_for_platform() —— 按平台限制切段
# ============================================================

def chunk_for_platform(
    text: str,
    max_chars: int = 2000,
) -> List[str]:
    """
    把长文本按平台字符限制切成多段。

    切分策略（M3 最小版本）：
      1. 先按段落（双换行）切
      2. 如果某一段本身就超过 max_chars，就按句号切
      3. 尽量让每段接近但不超过 max_chars

    参数：
        text: 要切分的文本
        max_chars: 每段最大字符数
                  Discord 限制是 2000，Telegram 限制是 4096
                  默认用 Discord 的限制（更严格）

    返回：
        切分后的文本段列表
    """
    if len(text) <= max_chars:
        # 不需要切分
        return [text]

    # 第 1 步：先按段落切（双换行）
    paragraphs = text.split("\n\n")

    chunks: List[str] = []
    current_chunk = ""

    for para in paragraphs:
        # 如果当前块 + 新段落不超过限制，就合并
        candidate = (current_chunk + "\n\n" + para).strip() if current_chunk else para

        if len(candidate) <= max_chars:
            current_chunk = candidate
        else:
            # 当前块已经满了，先保存
            if current_chunk:
                chunks.append(current_chunk)

            # 检查新段落本身是否超长
            if len(para) <= max_chars:
                current_chunk = para
            else:
                # 段落本身就超长，需要按句子切
                sentences = _split_into_sentences(para)
                current_chunk = ""
                for sentence in sentences:
                    candidate = (
                        (current_chunk + " " + sentence).strip()
                        if current_chunk
                        else sentence
                    )
                    if len(candidate) <= max_chars:
                        current_chunk = candidate
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        # 如果单个句子就超长，强制截断
                        if len(sentence) > max_chars:
                            while sentence:
                                chunks.append(sentence[:max_chars])
                                sentence = sentence[max_chars:]
                            current_chunk = ""
                        else:
                            current_chunk = sentence

    # 别忘了最后一块
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _split_into_sentences(text: str) -> List[str]:
    """
    简单的句子切分（按中英文句号切）。

    这是一个内部辅助函数，名字以 _ 开头表示"只在本文件内使用"。
    """
    # 按中文句号、英文句号+空格、换行 来切分
    parts = re.split(r"(?<=[。.!?！？])\s*|\n", text)
    # 过滤空字符串
    return [p.strip() for p in parts if p.strip()]
