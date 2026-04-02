import argparse
import os
from pathlib import Path
import re
import shutil

PROJECT_ROOT = Path(__file__).parent.parent.parent


def parse_env_file_with_comments(filepath):
    """
    解析 .env 文件，返回：
    - keys: 已存在的键集合
    - lines: 原始行列表（用于保留结构，但本脚本不修改原内容）
    """
    keys = set()
    key_value = {}
    if not os.path.exists(filepath):  # noqa: PTH110
        return keys, key_value
    with open(filepath, encoding="utf-8") as f:  # noqa: PTH123
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", stripped)
            if match:
                key = match.group(1)
                keys.add(key)
                # 提取等号后的值（去除前后空格）
                value = stripped.split("=", 1)[1].strip()
                key_value[key] = value
    return keys, key_value


def extract_all_key_defaults(filepath):
    """从 .env.example 提取所有 KEY -> 默认值 的映射（包括空值）"""
    defaults = {}
    if not os.path.exists(filepath):  # noqa: PTH110
        return defaults
    with open(filepath, encoding="utf-8") as f:  # noqa: PTH123
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", stripped)
            if match:
                key = match.group(1)
                value = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
                defaults[key] = value
    return defaults


def get_missing_keys(env_example_path, existing_keys):
    """找出缺失的键（按 .env.example 出现顺序）"""
    missing_keys = []
    seen = set()
    with open(env_example_path, encoding="utf-8") as f:  # noqa: PTH123
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", stripped)
            if match:
                key = match.group(1)
                if key not in seen and key not in existing_keys:
                    missing_keys.append(key)
                seen.add(key)
    return missing_keys


def process_user_input(key, default_val, env_val):
    """
    处理用户输入
    返回: (value, action) 其中 action 可以为 'continue', 'skip' 或 'skipall'
    """
    # 如果.env中有值，则直接使用该值，不再询问用户
    if env_val != "":
        print(f"✅ 自动设置 {key} = {env_val} (来自 .env)")
        return env_val, "continue"

    prompt = f"请输入 {key}"
    if default_val != "":
        prompt += f"（示例值: '{default_val}'）"
    prompt += "，留空跳过，输入 'skip' 跳过全部并拷贝.env.example，或输入 'skipall' 跳过全部: "

    user_input = input(prompt).strip()

    # 处理跳过全部的情况
    if user_input.lower() == "skipall":
        return None, "skipall"

    # 处理跳过当前项的情况
    if user_input.lower() in ("", "skip"):
        # 当用户选择跳过时，使用example中的默认值（如果有）
        print(f"✅ 设置 {key} = {default_val} (来自 .env.example)")
        return default_val, "continue"

    print(f"✅ 已设置 {key} = {user_input}")
    return user_input, "continue"


def write_new_entries(env_path, new_entries):
    """将新条目写入 .env 文件"""
    if not new_entries:
        print("\nℹ️  没有新增任何配置项。")
        return

    # 写入 .env
    mode = "a" if os.path.exists(env_path) else "w"  # noqa: PTH110
    with open(env_path, mode, encoding="utf-8") as f:  # noqa: PTH123
        if mode == "a" and os.path.getsize(env_path) > 0:  # noqa: PTH202
            f.write("\n")  # 确保换行分隔
        f.writelines(new_entries)

    print(f"\n🎉 成功将 {len(new_entries)} 项写入 {env_path}")


def main():
    parser = argparse.ArgumentParser(description="同步 .env.example 到 .env 文件")
    parser.add_argument(
        "--docker", action="store_true", help="将 .env.example 同步到 docker/.env 文件"
    )
    args = parser.parse_args()

    env_example_path = PROJECT_ROOT / "src" / ".env.example"

    # 根据参数决定目标文件路径
    if args.docker:
        env_path = PROJECT_ROOT / "docker" / ".env"
        print(f"🔧 Docker模式：将同步到 {env_path}")
    else:
        env_path = PROJECT_ROOT / "src" / ".env"

    if not os.path.exists(env_example_path):  # noqa: PTH110
        print(f"❌ 错误：{env_example_path} 文件不存在！")
        return

    # 检查是否应该跳过整个过程并直接拷贝文件
    skip_all = False

    # 获取当前 .env 中已有的键和值
    existing_keys, existing_values = parse_env_file_with_comments(env_path)
    # 获取 .env.example 中的所有键和默认值
    example_defaults = extract_all_key_defaults(env_example_path)

    # 找出缺失的键（按 .env.example 出现顺序）
    missing_keys = get_missing_keys(env_example_path, existing_keys)

    if not missing_keys:
        print("✅ .env 已包含所有配置项，无需补充。")
        return

    print(f"🔍 发现 {len(missing_keys)} 个缺失的配置项，将逐个询问：\n")

    new_entries = []

    for key in missing_keys:
        default_val = example_defaults.get(key, "")
        env_val = existing_values.get(key, "")

        value, action = process_user_input(key, default_val, env_val)

        if action == "skipall":
            skip_all = True
            break
        if action == "continue":
            new_entries.append(f"{key}={value}\n")

    # 如果用户选择跳过全部，则直接拷贝.env.example为.env
    if skip_all:
        shutil.copyfile(env_example_path, env_path)
        print(f"\n🔄 已跳过所有配置项，并将 {env_example_path} 拷贝为 {env_path}")
        return

    # 写入新的条目到.env文件
    write_new_entries(env_path, new_entries)


if __name__ == "__main__":
    main()
