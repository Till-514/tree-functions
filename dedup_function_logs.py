# -*- coding: utf-8 -*-
"""
日志去重工具 - 专为超大日志文件设计

功能：
- 流式读取日志文件，避免内存溢出
- 按函数ID去重，只保留第一次出现的记录
- 支持多文件合并去重
- 生成去重后的日志文件

用法:
    python dedup_function_logs.py --input <日志文件或目录> --output <输出文件>
    python dedup_function_logs.py -i logs/ -o deduped.txt
"""

import os
import sys
import argparse
import glob
import re
from typing import Set, List

# 日志行匹配正则表达式：Fn_数字ID_函数名
LOG_PATTERN = re.compile(r'^Fn_(\d+)_(\S+)')
# 进度显示间隔（处理多少行后显示一次进度）
PROGRESS_INTERVAL = 100000

def get_log_files(input_paths: List[str]) -> List[str]:
    """获取所有日志文件列表"""
    log_files = []
    
    for path in input_paths:
        if os.path.isfile(path):
            log_files.append(path)
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for file in files:
                    log_files.append(os.path.join(root, file))
        elif '*' in path or '?' in path:
            matched = glob.glob(path)
            log_files.extend(matched)
    
    return log_files

def deduplicate_logs(input_paths: List[str], output_file: str) -> bool:
    """去重日志文件（流式处理，避免内存溢出）"""
    log_files = get_log_files(input_paths)
    
    if not log_files:
        print(f"[ERROR] 未找到日志文件")
        return False
    
    print(f"[INFO] 找到 {len(log_files)} 个日志文件")
    print(f"[INFO] 输出文件: {output_file}")
    
    # 记录已出现的函数ID
    seen_function_ids: Set[int] = set()
    
    # 统计信息
    total_lines = 0
    valid_lines = 0
    deduped_lines = 0
    invalid_lines = 0
    
    # 创建输出目录
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    # 流式处理所有日志文件
    with open(output_file, 'w', encoding='utf-8') as out_f:
        for log_file in log_files:
            print(f"[INFO] 正在处理: {log_file}")
            
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as in_f:
                    for line in in_f:
                        total_lines += 1
                        
                        # 进度显示
                        if total_lines % PROGRESS_INTERVAL == 0:
                            print(f"[PROGRESS] 已处理 {total_lines:,} 行，去重后 {deduped_lines:,} 行")
                        
                        # 去除首尾空白
                        line = line.strip()
                        if not line:
                            continue
                        
                        # 匹配日志格式
                        match = LOG_PATTERN.match(line)
                        if not match:
                            invalid_lines += 1
                            continue
                        
                        # 提取函数ID
                        try:
                            func_id = int(match.group(1))
                            valid_lines += 1
                            
                            # 去重检查
                            if func_id not in seen_function_ids:
                                seen_function_ids.add(func_id)
                                out_f.write(line + '\n')
                                deduped_lines += 1
                            
                        except ValueError:
                            invalid_lines += 1
                            continue
                
            except Exception as e:
                print(f"[ERROR] 处理文件失败 {log_file}: {e}")
                continue
    
    # 打印统计信息
    print("\n" + "="*80)
    print("去重完成！统计信息：")
    print("="*80)
    print(f"总行数: {total_lines:,}")
    print(f"有效行数: {valid_lines:,}")
    print(f"无效行数: {invalid_lines:,}")
    print(f"去重后行数: {deduped_lines:,}")
    print(f"去重率: {(1 - deduped_lines/valid_lines)*100:.2f}%")
    print(f"唯一函数数量: {len(seen_function_ids)}")
    print("="*80)
    print(f"去重后的文件已保存到: {output_file}")
    print("="*80 + "\n")
    
    return True

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='日志去重工具 - 专为超大日志文件设计',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 去重单个文件
  python dedup_function_logs.py -i function_trace.txt -o deduped.txt
  
  # 去重整个目录
  python dedup_function_logs.py -i logs/ -o deduped.txt
  
  # 去重多个文件
  python dedup_function_logs.py -i log1.txt log2.txt -o deduped.txt
        """
    )
    
    parser.add_argument('-i', '--input', required=True, nargs='+',
                       help='输入日志文件或目录路径（支持多个）')
    parser.add_argument('-o', '--output', required=True,
                       help='输出文件路径')
    
    args = parser.parse_args()
    
    # 执行去重
    success = deduplicate_logs(args.input, args.output)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
