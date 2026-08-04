# -*- coding: utf-8 -*-
from tree_sitter import Parser, Language, Query, QueryCursor
import tree_sitter_cpp as tscpp
import os
import subprocess
import csv
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
GIT_REPO_ROOT = PROJECT_ROOT
SCAN_DIR = os.path.join(PROJECT_ROOT, "PxImport")
IS_GIT_REPO = True

def get_git_repo_root():
    return GIT_REPO_ROOT

if IS_GIT_REPO:
    print(f"检测到 Git 仓库，根目录：{GIT_REPO_ROOT}")
else:
    print("未检测到 Git 仓库，跳过修改人信息获取")

def get_git_blame_author(file_path, line_number):
    try:
        if not GIT_REPO_ROOT:
            return None
            
        result = subprocess.run(
            ['git', 'blame', '-w', '-L', f'{line_number},{line_number}', '--porcelain', '--', file_path],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=GIT_REPO_ROOT
        )
        
        if result.returncode != 0:
            return None
        
        for line in result.stdout.split('\n'):
            if line.startswith('author '):
                return line[7:].strip()
        
        return None
    except Exception:
        return None

def is_catch_node(node, code):
    p = node.parent
    while p:
        if p.type in ('catch_clause', 'try_statement', 'handler', 'except_clause'):
            return True
        p = p.parent
    
    start_byte = node.start_byte
    search_start = max(0, start_byte - 20)
    search_end = min(len(code), start_byte + 50)
    surrounding = code[search_start:search_end].decode('utf-8', errors='replace').lower()
    if 'catch' in surrounding and ('(' in surrounding or '{' in surrounding):
        return True
    
    return False

def is_inside_class_or_struct(node):
    p = node.parent
    while p:
        if p.type in ("class_specifier", "struct_specifier", "field_declaration_list"):
            return True
        p = p.parent
    return False

def find_descendant_by_type(node, node_type):
    if node.type == node_type:
        return node
    for child in node.children:
        found = find_descendant_by_type(child, node_type)
        if found:
            return found
    return None

def get_first_identifier(node):
    if node.type in (
        "identifier",
        "field_identifier",
        "qualified_identifier",
        "destructor_name",
        "operator_name",
    ):
        return node
    for child in node.children:
        found = get_first_identifier(child)
        if found:
            return found
    return None

def normalize_function_name(name):
    name = name.strip()
    if "::" in name:
        name = name.split("::")[-1]
    return name or None

def has_top_level_type_or_specifier(function_node):
    declarator = function_node.child_by_field_name("declarator")
    for child in function_node.children:
        if child == declarator:
            return False
        if child.type in (
            "primitive_type",
            "type_identifier",
            "qualified_identifier",
            "template_type",
            "placeholder_type_specifier",
            "sized_type_specifier",
            "type_qualifier",
            "storage_class_specifier",
            "virtual",
            "explicit_function_specifier",
        ):
            return True
    return False

def is_upper_macro_name(name):
    letters = [ch for ch in name if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)

def is_probably_macro_function_definition(function_node):
    declarator = function_node.child_by_field_name("declarator")
    if not declarator or declarator.type != "function_declarator":
        return False

    name_node = declarator.child_by_field_name("declarator") or get_first_identifier(declarator)
    if not name_node or name_node.type != "identifier":
        return False

    name = name_node.text.decode('utf-8', errors='replace').strip()
    if not is_upper_macro_name(name):
        return False

    return not has_top_level_type_or_specifier(function_node)

def has_function_body(function_node):
    for child in function_node.children:
        if child.type in ("compound_statement", "try_statement"):
            return True
    return False

def get_function_name(function_node):
    declarator = function_node.child_by_field_name("declarator")
    if not declarator:
        operator_cast = find_descendant_by_type(function_node, "operator_cast")
        if operator_cast:
            cast_text = operator_cast.text.decode('utf-8', errors='replace').strip()
            return cast_text.split("(", 1)[0].strip()
        return None

    function_declarator = find_descendant_by_type(declarator, "function_declarator")
    if not function_declarator:
        operator_cast = find_descendant_by_type(declarator, "operator_cast")
        if operator_cast:
            cast_text = operator_cast.text.decode('utf-8', errors='replace').strip()
            return cast_text.split("(", 1)[0].strip()
        return None

    name_node = function_declarator.child_by_field_name("declarator")
    if not name_node:
        name_node = get_first_identifier(function_declarator)

    if not name_node:
        return None

    name = name_node.text.decode('utf-8', errors='replace').strip()
    return normalize_function_name(name)

cpp_lang = Language(tscpp.language())
parser = Parser(cpp_lang)

query = Query(
    cpp_lang,
    """
    (function_definition) @func
    """
)

query_cursor = QueryCursor(query)

scan_results = []

print("="*80)
print("函数扫描工具")
print("="*80)
print("扫描目录：", SCAN_DIR)
print("-"*80)
print("%-8s %-30s %-45s %-8s %-20s" % ("编号", "函数名", "文件", "行号", "修改人"))
print("-"*80)

count = 0
EXCLUDE_DIRS = {'.venv', '__pycache__', '.git', 'node_modules', 'build', 'dist'}

if not os.path.exists(SCAN_DIR):
    print(f"错误：扫描目录 {SCAN_DIR} 不存在！")
    exit(1)

for root, dirs, files in os.walk(SCAN_DIR):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    
    for file in files:
        if not file.lower().endswith((".cpp", ".h", ".hpp", ".c")):
            continue

        path = os.path.join(root, file)

        try:
            with open(path, "rb") as f:
                code = f.read()

            if b'\x00' in code:
                continue

            tree = parser.parse(code)
            captures = []
            for _, captures_dict in query_cursor.matches(tree.root_node):
                for capture_name, nodes in captures_dict.items():
                    for node in nodes:
                        captures.append((node, capture_name))

            rel_path = os.path.relpath(path, GIT_REPO_ROOT).replace('\\', '/') if IS_GIT_REPO else os.path.relpath(path, PROJECT_ROOT).replace('\\', '/')

            for node, _ in captures:
                if not has_function_body(node):
                    continue

                if is_catch_node(node, code):
                    continue

                if is_inside_class_or_struct(node):
                    continue

                if is_probably_macro_function_definition(node):
                    continue

                fname = get_function_name(node)
                if not fname:
                    continue

                line = node.start_point[0] + 1
                
                if IS_GIT_REPO:
                    author = get_git_blame_author(rel_path, line)
                else:
                    author = None
                
                print("%-8d %-30s %-45s %-8d %-20s" % (count + 1, fname, rel_path, line, author or ""))
                
                scan_results.append({
                    "function_number": count + 1,
                    "function_name": fname,
                    "file_path": rel_path,
                    "line_number": line,
                    "author": author
                })
                
                count += 1

        except Exception as e:
            print("解析 %s 时出错: %s" % (os.path.relpath(path, PROJECT_ROOT), e))
            continue

print("-"*80)
print("扫描完成！共找到 %d 个函数" % count)
print("="*80)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

csv_file = os.path.join(PROJECT_ROOT, f"function_scan_result_{timestamp}.csv")
with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(["函数名", "文件", "行号", "修改人"])
    for result in scan_results:
        writer.writerow([
            result["function_name"],
            result["file_path"],
            result["line_number"],
            result["author"] or ""
        ])
print("CSV 结果已保存到: %s" % csv_file)

json_file = os.path.join(PROJECT_ROOT, f"function_scan_result_{timestamp}.json")
with open(json_file, 'w', encoding='utf-8') as f:
    json.dump({
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_directory": SCAN_DIR,
        "git_repo_root": GIT_REPO_ROOT if IS_GIT_REPO else None,
        "total_functions": count,
        "functions": scan_results
    }, f, indent=2, ensure_ascii=False)
print("JSON 结果已保存到: %s" % json_file)
