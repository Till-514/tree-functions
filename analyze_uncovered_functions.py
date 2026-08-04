# -*- coding: utf-8 -*-
"""
高性能函数调用覆盖率分析工具
专为海量函数（几千个）和超大日志文件（GB级）设计

核心特性：
- 流式逐行读取日志，避免内存溢出
- 支持多日志文件批量合并统计
- 覆盖率模式和频次统计模式可切换
- 分层统计：全局、文件、模块维度
- 生成Markdown报告和CSV明细文件

用法:
    python analyze_uncovered_functions.py --scan <函数表文件> --log <日志文件或目录> --output <输出目录>
    python analyze_uncovered_functions.py -s functions.json -l logs/ -o reports/
"""

import os
import sys
import json
import csv
import argparse
import glob
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, Set, List, Tuple, Optional

# ==================== 配置常量 ====================
# 日志行匹配正则表达式：Fn_数字ID_函数名
LOG_PATTERN = re.compile(r'^Fn_(\d+)_(\S+)')
# 进度显示间隔（处理多少行后显示一次进度）
PROGRESS_INTERVAL = 10000
# ================================================

class FunctionCoverageAnalyzer:
    """函数调用覆盖率分析器"""
    
    def __init__(self, scan_file: str, log_paths: List[str], output_dir: str, 
                 mode: str = 'coverage', whitelist_file: Optional[str] = None):
        """
        初始化分析器
        
        Args:
            scan_file: 静态函数表文件路径（JSON格式）
            log_paths: 日志文件或目录路径列表
            output_dir: 输出报告目录
            mode: 统计模式，'coverage'（覆盖率）或 'frequency'（频次统计）
            whitelist_file: 白名单文件路径（可选）
        """
        self.scan_file = scan_file
        self.log_paths = log_paths
        self.output_dir = output_dir
        self.mode = mode
        self.whitelist_file = whitelist_file
        
        # 数据结构
        self.function_map: Dict[int, dict] = {}  # 函数ID -> 函数信息
        self.called_functions: Set[int] = set()  # 被调用的函数ID集合（覆盖率模式）
        self.function_call_counts: Dict[int, int] = defaultdict(int)  # 函数调用次数（频次模式）
        self.whitelist: Set[int] = set()  # 白名单函数ID
        
        # 统计结果
        self.stats = {
            'total_functions': 0,
            'called_functions': 0,
            'total_calls': 0,
            'coverage_rate': 0.0,
            'file_stats': defaultdict(dict),
            'module_stats': defaultdict(dict)
        }
        
    def load_scan_result(self) -> bool:
        """
        加载静态函数表（流式读取，避免内存溢出）
        
        Returns:
            bool: 是否加载成功
        """
        print(f"[INFO] 正在加载函数表: {self.scan_file}")
        
        try:
            if not os.path.exists(self.scan_file):
                print(f"[ERROR] 函数表文件不存在: {self.scan_file}")
                return False
            
            with open(self.scan_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            
            # 构建函数映射表。兼容两种扫描结果：
            # 1. V4 日志注入脚本生成的 JSON：包含 function_number
            # 2. 旧版扫描脚本生成的 JSON：不包含 function_number，按顺序补 1-based ID
            for index, func in enumerate(data.get('functions', []), 1):
                func_id = func.get('function_number', index)
                self.function_map[func_id] = {
                    'name': func.get('function_name', ''),
                    'file': func.get('file_path', ''),
                    'line': func.get('line_number', 0),
                    'module': self._extract_module(func.get('file_path', '')),
                    'author': func.get('author')
                }
            
            self.stats['total_functions'] = len(self.function_map)
            print(f"[INFO] 函数表加载完成，共 {self.stats['total_functions']} 个函数")
            return True
            
        except json.JSONDecodeError as e:
            print(f"[ERROR] 函数表JSON格式错误: {e}")
            return False
        except Exception as e:
            print(f"[ERROR] 加载函数表失败: {e}")
            return False
    
    def load_whitelist(self) -> bool:
        """
        加载白名单文件
        
        Returns:
            bool: 是否加载成功
        """
        if not self.whitelist_file:
            return True
        
        print(f"[INFO] 正在加载白名单: {self.whitelist_file}")
        
        try:
            if not os.path.exists(self.whitelist_file):
                print(f"[WARNING] 白名单文件不存在: {self.whitelist_file}")
                return True
            
            with open(self.whitelist_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        try:
                            func_id = int(line)
                            self.whitelist.add(func_id)
                        except ValueError:
                            continue
            
            print(f"[INFO] 白名单加载完成，共 {len(self.whitelist)} 个函数")
            return True
            
        except Exception as e:
            print(f"[ERROR] 加载白名单失败: {e}")
            return False
    
    def _extract_module(self, file_path: str) -> str:
        """
        从文件路径提取模块名
        
        Args:
            file_path: 文件路径
            
        Returns:
            str: 模块名
        """
        # 简单实现：取第一层目录作为模块名
        parts = file_path.replace('\\', '/').split('/')
        if len(parts) > 1:
            return parts[0]
        return 'default'
    
    def _get_log_files(self) -> List[str]:
        """
        获取所有日志文件列表
        
        Returns:
            List[str]: 日志文件路径列表
        """
        log_files = []
        
        for path in self.log_paths:
            if os.path.isfile(path):
                log_files.append(path)
            elif os.path.isdir(path):
                # 递归查找目录下所有文件
                for root, dirs, files in os.walk(path):
                    for file in files:
                        log_files.append(os.path.join(root, file))
            elif '*' in path or '?' in path:
                # 支持通配符
                matched = glob.glob(path)
                log_files.extend(matched)
        
        return log_files
    
    def parse_log_files(self) -> bool:
        """
        流式解析日志文件（逐行读取，避免内存溢出）
        
        Returns:
            bool: 是否解析成功
        """
        log_files = self._get_log_files()
        
        if not log_files:
            print(f"[ERROR] 未找到日志文件")
            return False
        
        print(f"[INFO] 找到 {len(log_files)} 个日志文件")
        
        total_lines = 0
        valid_lines = 0
        invalid_lines = 0
        
        for log_file in log_files:
            print(f"[INFO] 正在解析日志文件: {log_file}")
            
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        total_lines += 1
                        
                        # 进度显示
                        if total_lines % PROGRESS_INTERVAL == 0:
                            print(f"[PROGRESS] 已处理 {total_lines} 行，有效 {valid_lines} 行")
                        
                        # 去除首尾空白
                        line = line.strip().lstrip('\ufeff')
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
                            
                            # 根据模式统计
                            if self.mode == 'coverage':
                                self.called_functions.add(func_id)
                            elif self.mode == 'frequency':
                                self.function_call_counts[func_id] += 1
                            
                            self.stats['total_calls'] += 1
                            
                        except ValueError:
                            invalid_lines += 1
                            continue
                
            except Exception as e:
                print(f"[ERROR] 解析日志文件失败 {log_file}: {e}")
                continue
        
        print(f"[INFO] 日志解析完成：总行数 {total_lines}，有效 {valid_lines}，无效 {invalid_lines}")
        return True
    
    def calculate_statistics(self):
        """计算统计指标"""
        print("[INFO] 正在计算统计指标...")
        
        # 全局统计
        if self.mode == 'coverage':
            self.stats['called_functions'] = len(self.called_functions)
        elif self.mode == 'frequency':
            self.stats['called_functions'] = len(self.function_call_counts)
        
        # 计算覆盖率（排除白名单）
        effective_total = self.stats['total_functions'] - len(self.whitelist)
        effective_called = self.stats['called_functions'] - len(self.called_functions & self.whitelist)
        
        if effective_total > 0:
            self.stats['coverage_rate'] = (effective_called / effective_total) * 100
        else:
            self.stats['coverage_rate'] = 0.0
        
        # 分文件统计
        for func_id, func_info in self.function_map.items():
            file_path = func_info['file']
            
            if file_path not in self.stats['file_stats']:
                self.stats['file_stats'][file_path] = {
                    'total': 0,
                    'called': 0,
                    'calls': 0,
                    'functions': []
                }
            
            self.stats['file_stats'][file_path]['total'] += 1
            self.stats['file_stats'][file_path]['functions'].append(func_id)
            
            if self.mode == 'coverage':
                if func_id in self.called_functions:
                    self.stats['file_stats'][file_path]['called'] += 1
            elif self.mode == 'frequency':
                if func_id in self.function_call_counts:
                    self.stats['file_stats'][file_path]['called'] += 1
                    self.stats['file_stats'][file_path]['calls'] += self.function_call_counts[func_id]
        
        # 分模块统计
        for file_path, file_stat in self.stats['file_stats'].items():
            module = self._extract_module(file_path)
            
            if module not in self.stats['module_stats']:
                self.stats['module_stats'][module] = {
                    'total': 0,
                    'called': 0,
                    'calls': 0,
                    'files': []
                }
            
            self.stats['module_stats'][module]['total'] += file_stat['total']
            self.stats['module_stats'][module]['called'] += file_stat['called']
            self.stats['module_stats'][module]['calls'] += file_stat['calls']
            self.stats['module_stats'][module]['files'].append(file_path)
        
        print("[INFO] 统计指标计算完成")
    
    def generate_reports(self):
        """生成未覆盖函数结果文件"""
        print(f"[INFO] 正在生成报告到目录: {self.output_dir}")
        
        # 创建输出目录
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 生成时间戳
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        self._generate_uncovered_functions_csv(timestamp)

        print(f"[INFO] 未覆盖函数结果生成完成")

    def _is_function_covered(self, func_id: int) -> bool:
        if self.mode == 'coverage':
            return func_id in self.called_functions
        return func_id in self.function_call_counts

    def _generate_uncovered_functions_csv(self, timestamp: str):
        """只输出未覆盖函数：函数名、函数位置、修改人"""
        csv_file = os.path.join(self.output_dir, f"uncovered_functions_{timestamp}.csv")

        uncovered_funcs = [
            (func_id, func_info)
            for func_id, func_info in self.function_map.items()
            if not self._is_function_covered(func_id)
        ]
        uncovered_funcs.sort(key=lambda x: (x[1].get('file', ''), x[1].get('line', 0), x[1].get('name', '')))

        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['函数名', '函数位置', '修改人'])

            for _, func_info in uncovered_funcs:
                file_path = func_info.get('file', '')
                line_number = func_info.get('line', 0)
                location = "%s:%s" % (file_path, line_number)
                writer.writerow([
                    func_info.get('name', ''),
                    location,
                    func_info.get('author') or ''
                ])

        print(f"[INFO] 未覆盖函数CSV已生成: {csv_file}")
    
    def _generate_markdown_report(self, timestamp: str):
        """生成Markdown总报告"""
        report_file = os.path.join(self.output_dir, f"coverage_report_{timestamp}.md")
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# 函数调用覆盖率分析报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"**统计模式**: {self.mode}\n\n")
            
            # 全局概览
            f.write("## 1. 全局概览\n\n")
            f.write("| 指标 | 数值 |\n")
            f.write("|------|------|\n")
            f.write(f"| 项目全部函数总数 | {self.stats['total_functions']} |\n")
            f.write(f"| 实际执行过的唯一函数数量 | {self.stats['called_functions']} |\n")
            f.write(f"| 全局函数调用覆盖率 | {self.stats['coverage_rate']:.2f}% |\n")
            f.write(f"| 全局总调用次数 | {self.stats['total_calls']} |\n")
            f.write(f"| 白名单函数数量 | {len(self.whitelist)} |\n")
            
            # 高频函数TOP 50
            if self.mode == 'frequency':
                f.write("\n## 2. 高频调用函数 TOP 50\n\n")
                f.write("| 排名 | 函数ID | 函数名 | 文件 | 调用次数 |\n")
                f.write("|------|--------|--------|------|----------|\n")
                
                sorted_funcs = sorted(self.function_call_counts.items(), 
                                     key=lambda x: x[1], reverse=True)[:50]
                
                for rank, (func_id, count) in enumerate(sorted_funcs, 1):
                    func_info = self.function_map.get(func_id, {})
                    f.write(f"| {rank} | {func_id} | {func_info.get('name', 'N/A')} | "
                           f"{func_info.get('file', 'N/A')} | {count} |\n")
            
            # 模块覆盖率
            f.write("\n## 3. 模块覆盖率统计\n\n")
            f.write("| 模块 | 函数总数 | 已调用 | 覆盖率 | 总调用次数 |\n")
            f.write("|------|----------|--------|--------|------------|\n")
            
            for module, stat in sorted(self.stats['module_stats'].items()):
                rate = (stat['called'] / stat['total'] * 100) if stat['total'] > 0 else 0
                f.write(f"| {module} | {stat['total']} | {stat['called']} | "
                       f"{rate:.2f}% | {stat['calls']} |\n")
            
            # 未覆盖函数数量汇总
            uncalled_count = self.stats['total_functions'] - self.stats['called_functions']
            f.write(f"\n## 4. 未覆盖函数汇总\n\n")
            f.write(f"**未覆盖函数总数**: {uncalled_count}\n\n")
        
        print(f"[INFO] Markdown报告已生成: {report_file}")
    
    def _generate_function_detail_csv(self, timestamp: str):
        """生成全函数明细CSV"""
        csv_file = os.path.join(self.output_dir, f"function_detail_{timestamp}.csv")
        
        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['函数ID', '函数名', '所属文件', '所属模块', '是否被调用', '总调用次数'])
            
            for func_id in sorted(self.function_map.keys()):
                func_info = self.function_map[func_id]
                
                if self.mode == 'coverage':
                    is_called = func_id in self.called_functions
                    call_count = 1 if is_called else 0
                else:
                    is_called = func_id in self.function_call_counts
                    call_count = self.function_call_counts.get(func_id, 0)
                
                writer.writerow([
                    func_id,
                    func_info['name'],
                    func_info['file'],
                    func_info['module'],
                    '是' if is_called else '否',
                    call_count
                ])
        
        print(f"[INFO] 函数明细CSV已生成: {csv_file}")
    
    def _generate_uncalled_functions_csv(self, timestamp: str):
        """生成未覆盖函数CSV"""
        csv_file = os.path.join(self.output_dir, f"uncalled_functions_{timestamp}.csv")
        
        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['函数ID', '函数名', '所属文件', '所属模块'])
            
            # 按文件分组排序
            uncalled_funcs = []
            for func_id, func_info in self.function_map.items():
                if self.mode == 'coverage':
                    is_called = func_id in self.called_functions
                else:
                    is_called = func_id in self.function_call_counts
                
                if not is_called:
                    uncalled_funcs.append((func_id, func_info))
            
            # 按文件路径排序
            uncalled_funcs.sort(key=lambda x: x[1]['file'])
            
            for func_id, func_info in uncalled_funcs:
                writer.writerow([
                    func_id,
                    func_info['name'],
                    func_info['file'],
                    func_info['module']
                ])
        
        print(f"[INFO] 未覆盖函数CSV已生成: {csv_file}")
    
    def _generate_coverage_summary_csv(self, timestamp: str):
        """生成覆盖率汇总CSV"""
        csv_file = os.path.join(self.output_dir, f"coverage_summary_{timestamp}.csv")
        
        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            
            # 文件维度统计
            writer.writerow(['类型', '名称', '函数总数', '已调用', '覆盖率', '总调用次数'])
            
            for file_path, stat in sorted(self.stats['file_stats'].items()):
                rate = (stat['called'] / stat['total'] * 100) if stat['total'] > 0 else 0
                writer.writerow(['文件', file_path, stat['total'], stat['called'], 
                               f"{rate:.2f}%", stat['calls']])
            
            # 模块维度统计
            for module, stat in sorted(self.stats['module_stats'].items()):
                rate = (stat['called'] / stat['total'] * 100) if stat['total'] > 0 else 0
                writer.writerow(['模块', module, stat['total'], stat['called'], 
                               f"{rate:.2f}%", stat['calls']])
        
        print(f"[INFO] 覆盖率汇总CSV已生成: {csv_file}")
    
    def print_summary(self):
        """打印控制台摘要"""
        print("\n" + "="*80)
        print("分析完成！核心统计结果：")
        print("="*80)
        print(f"项目全部函数总数: {self.stats['total_functions']}")
        print(f"实际执行过的唯一函数数量: {self.stats['called_functions']}")
        print(f"全局函数调用覆盖率: {self.stats['coverage_rate']:.2f}%")
        print(f"全局总调用次数: {self.stats['total_calls']}")
        print(f"未覆盖函数数量: {self.stats['total_functions'] - self.stats['called_functions']}")
        print("="*80)
        print(f"详细报告已保存到: {self.output_dir}")
        print("="*80 + "\n")
    
    def run(self) -> bool:
        """
        执行完整分析流程
        
        Returns:
            bool: 是否执行成功
        """
        # 1. 加载函数表
        if not self.load_scan_result():
            return False
        
        # 2. 加载白名单
        if not self.load_whitelist():
            return False
        
        # 3. 解析日志文件
        if not self.parse_log_files():
            return False
        
        # 4. 计算统计指标
        self.calculate_statistics()
        
        # 5. 生成报告
        self.generate_reports()
        
        # 6. 打印摘要
        self.print_summary()
        
        return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='高性能函数调用覆盖率分析工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 覆盖率模式（统计哪些函数被调用）
  python analyze_uncovered_functions.py -s functions.json -l logs/ -o reports/
  
  # 频次统计模式（统计每个函数调用次数）
  python analyze_uncovered_functions.py -s functions.json -l logs/ -o reports/ --mode frequency
  
  # 使用白名单
  python analyze_uncovered_functions.py -s functions.json -l logs/ -o reports/ --whitelist whitelist.txt
        """
    )
    
    parser.add_argument('-s', '--scan', required=True, 
                       help='静态函数表文件路径（JSON格式）')
    parser.add_argument('-l', '--log', required=True, nargs='+',
                       help='日志文件或目录路径（支持多个）')
    parser.add_argument('-o', '--output', required=True,
                       help='输出报告目录')
    parser.add_argument('--mode', choices=['coverage', 'frequency'], 
                       default='coverage',
                       help='统计模式：coverage（覆盖率）或 frequency（频次统计）')
    parser.add_argument('--whitelist', 
                       help='白名单文件路径（每行一个函数ID）')
    
    args = parser.parse_args()
    
    # 创建分析器实例
    analyzer = FunctionCoverageAnalyzer(
        scan_file=args.scan,
        log_paths=args.log,
        output_dir=args.output,
        mode=args.mode,
        whitelist_file=args.whitelist
    )
    
    # 执行分析
    success = analyzer.run()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
