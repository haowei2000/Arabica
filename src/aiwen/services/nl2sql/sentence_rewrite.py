import asyncio
import logging
import re

import jieba
import jieba.posseg as pseg
from fuzzywuzzy import fuzz
from pypinyin import lazy_pinyin

from aiwen.services.nl2sql.dimension_registry import get_registry
from aiwen.services.nl2sql.indi_r_sub_dim import _find_dimension_getter

# 设置logger
logger = logging.getLogger(__name__)

# ==================== 配置常量 ====================

PRESET_POS = {
    'noun_only': {'n', 'nr', 'ns', 'nt', 'nz', 'eng', 'x'},
    'entity_only': {'nr', 'ns', 'nt', 'nz'},
    'all_noun': {'n', 'nr', 'ns', 'nt', 'nz', 'nw', 'vn', 'an', 'eng', 'x'},
}


# ==================== 相似度计算函数 ====================

def calculate_pinyin_similarity(text, candidate):
    """计算包含拼音的相似度"""
    chinese_parts = re.findall(r'[\u4e00-\u9fff]+', text)
    pinyin_parts = re.findall(r'[a-zA-Z]+', text)

    chinese_str = ''.join(chinese_parts)
    pinyin_str = ''.join(pinyin_parts).lower()
    candidate_pinyin = ''.join(lazy_pinyin(candidate)).lower()

    # 情况1: 中文+拼音 (如 "笔记ben")
    if chinese_str and pinyin_str:
        if not candidate.startswith(chinese_str):
            return 0

        remaining = candidate[len(chinese_str):]
        remaining_pinyin = ''.join(lazy_pinyin(remaining)).lower()

        if remaining_pinyin == pinyin_str:
            return 95
        elif remaining_pinyin.startswith(pinyin_str):
            return 85
        else:
            sim = fuzz.ratio(pinyin_str, remaining_pinyin)
            return 75 if sim >= 80 else 0

    # 情况2: 纯拼音 (如 "shouji")
    elif pinyin_str:
        if candidate_pinyin == pinyin_str:
            return 90
        elif candidate_pinyin.endswith(pinyin_str):
            return 80
        else:
            return fuzz.ratio(pinyin_str, candidate_pinyin) * 0.8

    # 情况3: 大小写变化 (如 "oppO")
    else:
        return fuzz.ratio(text.lower(), candidate.lower())


def calculate_chinese_similarity(text, candidate, span):
    """计算纯中文的相似度"""
    char_score = fuzz.ratio(text, candidate)
    pinyin_score = fuzz.ratio(
        ''.join(lazy_pinyin(text)),
        ''.join(lazy_pinyin(candidate))
    )

    # 基础得分
    base_score = char_score * 0.6 + pinyin_score * 0.4

    # 如果是多词组合（span > 2），稍微降低阈值要求
    if span > 2:
        base_score *= 0.95

    return base_score


def calculate_similarity(text, candidate, span):
    """计算文本与候选词的相似度"""
    has_pinyin = bool(re.search(r'[a-zA-Z]', text))

    if has_pinyin:
        return calculate_pinyin_similarity(text, candidate)
    else:
        return calculate_chinese_similarity(text, candidate, span)


# ==================== 词性验证函数 ====================

def is_valid_combination(pos_tags, start_idx, span, allowed_pos):
    """
    检查词性组合是否有效
    只允许连续的名词和动词组合
    """
    # 第一个词必须是允许的词性（名词）
    if pos_tags[start_idx] not in allowed_pos:
        return False

    # 允许的词性：名词和动词
    allowed_for_combination = allowed_pos | {'v', 'vn', 'vd'}  # 添加动词相关词性

    # 检查所有词是否都是名词或动词
    for j in range(span):
        pos = pos_tags[start_idx + j]
        if pos not in allowed_for_combination:
            return False

    # 统计名词数量（至少要有1个名词）
    noun_count = sum(1 for j in range(span)
                     if pos_tags[start_idx + j] in allowed_pos)

    if noun_count < 1:
        return False

    return True


# ==================== 匹配查找函数 ====================

def find_best_match(words, pos_tags, start_idx, processed, word_list,
                    allowed_pos, max_span=5, max_length_diff=4):
    """查找从start_idx开始的最佳匹配，允许前后各2个名词"""
    best_match = None
    best_score = 0
    best_span = 1

    # 尝试1到max_span个连续词的组合
    for span in range(1, min(max_span + 1, len(words) - start_idx + 1)):
        # 检查是否已处理
        if any((start_idx + j) in processed for j in range(span)):
            break

        # 验证词性组合
        if not is_valid_combination(pos_tags, start_idx, span, allowed_pos):
            continue

        combined = ''.join(words[start_idx:start_idx + span])

        # 长度检查
        if len(combined) < 2 or len(combined) > 20:
            continue

        # 在词表中查找最佳匹配
        for candidate in word_list:
            if abs(len(combined) - len(candidate)) > max_length_diff:
                continue

            score = calculate_similarity(combined, candidate, span)

            if score > best_score:
                best_score = score
                best_match = candidate
                best_span = span

    return best_match, best_score, best_span


# ==================== 词表加载函数 ====================

def add_words_to_jieba(word_list):
    """将词表加入jieba字典"""
    for word in word_list:
        jieba.add_word(word, freq=10000)


# ==================== 核心替换函数 ====================

def replace_text(text, word_list, threshold=70, pos_mode='noun_only',
                 verbose=False, max_span=5, max_length_diff=4):
    """
    替换文本中的相似词

    参数:
        text: 待处理文本
        word_list: 参考词列表
        threshold: 相似度阈值 (0-100)
        pos_mode: 词性模式 ('noun_only', 'entity_only', 'all_noun')
        verbose: 是否显示详细信息
        max_span: 最大组合跨度
        max_length_diff: 允许的最大长度差异

    返回:
        (替换后的文本, 匹配详情列表)
    """
    # 设置允许的词性
    if isinstance(pos_mode, str):
        allowed_pos = PRESET_POS.get(pos_mode, PRESET_POS['noun_only'])
    else:
        allowed_pos = pos_mode

    # 将词表加入jieba字典
    add_words_to_jieba(word_list)

    # 词性标注
    words_with_pos = list(pseg.cut(text))
    words = [w for w, _ in words_with_pos]
    pos_tags = [p for _, p in words_with_pos]

    if verbose:
        logger.debug("分词及词性: %s", list(zip(words, pos_tags)))

    result = []
    matches = []
    processed = set()

    i = 0
    while i < len(words):
        if i in processed:
            i += 1
            continue

        word = words[i]
        pos = pos_tags[i]

        # 跳过空白
        if not word.strip():
            result.append(word)
            i += 1
            continue

        # 只处理允许的词性
        if pos not in allowed_pos:
            result.append(word)
            i += 1
            continue

        # 查找最佳匹配
        best_match, best_score, best_span = find_best_match(
            words, pos_tags, i, processed, word_list,
            allowed_pos, max_span, max_length_diff
        )

        # 应用匹配
        if best_score >= threshold and best_match:
            original = ''.join(words[i:i + best_span])
            result.append(best_match)

            if original != best_match:
                matches.append({
                    'original': original,
                    'replaced': best_match,
                    'score': round(best_score, 2),
                    'pos': '+'.join(pos_tags[i:i + best_span]),
                    'span': best_span
                })

            # 标记已处理
            for j in range(i, i + best_span):
                processed.add(j)
            i += best_span
        else:
            result.append(word)
            i += 1

    return ''.join(result), matches


# ==================== 异步维度值获取函数 ====================

async def get_dimension_values_safe(dimension_name, dimension_registry):
    """安全地获取单个维度的值"""
    try:
        _, getter_func = _find_dimension_getter(dimension_name, dimension_registry)
        if getter_func:
            values = await getter_func()
            return values if values else []
        return []
    except Exception as e:
        logger.debug(f"Error getting dimension '{dimension_name}': {e}")
        import traceback
        traceback.print_exc()
        return []


async def get_all_dimension_values():
    """
    异步获取所有维度的值
    确保在同一个事件循环中执行
    """
    dimension_registry = get_registry()
    dimensions = dimension_registry.list_dimensions()

    # 并发执行，但在同一个事件循环中
    tasks = [get_dimension_values_safe(dim, dimension_registry) for dim in dimensions]
    results = await asyncio.gather(*tasks)

    # 展平结果
    all_values = []
    for result in results:
        if result:
            all_values.extend(result)

    return all_values


# ==================== 主要API函数 ====================

async def smart_replace(text, threshold=70, pos_mode='noun_only',
                        verbose=False, max_span=5):
    """
    异步智能替换函数

    参数:
        text: 待处理文本
        threshold: 相似度阈值 (0-100)
        pos_mode: 词性模式 ('noun_only', 'entity_only', 'all_noun')
        verbose: 是否显示详细信息
        max_span: 最大组合跨度（默认5，即前后各2个名词）

    返回:
        (替换后的文本, 匹配详情列表)
    """
    # 异步获取所有维度值
    all_values = await get_all_dimension_values()

    if verbose:
        logger.debug("获取到 %d 个维度值", len(all_values))
        logger.debug(all_values)
    # 执行替换
    return replace_text(
        text=text,
        word_list=all_values,
        threshold=threshold,
        pos_mode=pos_mode,
        verbose=verbose,
        max_span=max_span
    )


def smart_replace_sync(text, threshold=70, pos_mode='noun_only',
                       verbose=False, max_span=5):
    """
    同步版本的智能替换

    注意：这会创建新的事件循环，不要在已有异步上下文中使用
    """
    return asyncio.run(smart_replace(text, threshold, pos_mode, verbose, max_span))


# ==================== 工具函数 ====================

def replace_with_custom_wordlist(text, word_list, threshold=70,
                                 pos_mode='noun_only', verbose=False, max_span=5):
    """
    使用自定义词表进行替换（同步版本）

    参数:
        text: 待处理文本
        word_list: 自定义词表
        threshold: 相似度阈值
        pos_mode: 词性模式
        verbose: 是否显示详细信息
        max_span: 最大组合跨度

    返回:
        (替换后的文本, 匹配详情列表)
    """
    return replace_text(
        text=text,
        word_list=word_list,
        threshold=threshold,
        pos_mode=pos_mode,
        verbose=verbose,
        max_span=max_span
    )


# ==================== 测试函数 ====================
async def check_smart_replace():
    """测试智能替换功能"""
    test_cases = [
        "查询车桥机加成线的产量",
        "车桥装配总线产量是多少",
        "车桥装配线的机加产量",
    ]

    for case in test_cases:
        logger.debug("=" * 70)
        logger.debug("原始文本: %s", case)

        try:
            result, matches = await smart_replace(case, verbose=True, max_span=5)
            logger.debug("处理后文本: %s", result)

            if matches:
                logger.debug("匹配详情:")
                for match in matches:
                    logger.debug("  '%s' -> '%s' (得分: %s, 词性: %s, 跨度: %s)",
                                match['original'], match['replaced'], match['score'],
                                match['pos'], match['span'])
        except Exception as e:
            logger.debug("错误: %s", e)
            import traceback
            traceback.print_exc()

        logger.debug("")


# ==================== 主程序入口 ====================

if __name__ == "__main__":
    import importlib
    import aiwen.services.nl2sql.dimension_registry.dimensions

    importlib.reload(aiwen.services.nl2sql.dimension_registry.dimensions)

    # 测试1: 使用维度数据
    logger.debug("【测试1: 使用维度数据】")
    asyncio.run(check_smart_replace())
