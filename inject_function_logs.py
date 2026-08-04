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

LOG_DIR = "C:/Users/Public/"
TRACE_LOG_MACRO = "PX_EXPORT"
LOG_FILE_PREFIX = "FnTrace_"

EXCLUDE_DIRS = {'.venv', '__pycache__', '.git', 'node_modules', 'build', 'dist'}
SOURCE_EXTENSIONS = (".cpp", ".h", ".hpp", ".c", ".cc", ".cxx")


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
        if p.type in ('catch_clause', 'handler', 'except_clause'):
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


def find_function_body(function_node):
    for child in function_node.children:
        if child.type == "compound_statement":
            return child
        if child.type == "try_statement":
            return find_descendant_by_type(child, "compound_statement")
    return None


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

    name_node = function_declarator.child_by_field_name("declarator") or get_first_identifier(function_declarator)
    if not name_node:
        return None

    return normalize_function_name(name_node.text.decode('utf-8', errors='replace'))


def find_first_non_space(content, start):
    while start < len(content) and content[start:start + 1] in b' \t\r\n':
        start += 1
    return start


def find_matching_brace(content, open_brace_pos):
    depth = 0
    i = open_brace_pos
    while i < len(content):
        ch = content[i:i + 1]
        if ch == b'{':
            depth += 1
        elif ch == b'}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def find_existing_log_block(content, insert_pos):
    start = find_first_non_space(content, insert_pos)

    if content[start:].startswith(("#ifdef %s" % TRACE_LOG_MACRO).encode('utf-8')):
        endif_pos = content.find(b"#endif", start)
        if endif_pos != -1 and LOG_FILE_PREFIX.encode('utf-8') in content[start:endif_pos]:
            line_end = content.find(b"\n", endif_pos)
            return "wrapped", start, len(content) if line_end == -1 else line_end + 1

    log_pos = content.find(LOG_FILE_PREFIX.encode('utf-8'), start, start + 1024)
    if log_pos == -1:
        return "none", start, start

    block_start = content.rfind(b'{', start, log_pos)
    if block_start == -1 or block_start != start:
        return "none", start, start

    block_end = find_matching_brace(content, block_start)
    if block_end == -1:
        return "none", start, start

    block_content = content[block_start:block_end]
    if b"std::ofstream outFile" not in block_content or b'outFile << "Fn_' not in block_content:
        return "none", start, start

    return "bare", block_start, block_end + 1


def build_log_code(function_number):
    return '''\n#ifdef %s
{
    std::string logFile = std::string(R"(%s)") + "%s" + std::to_string(GetCurrentProcessId()) + ".log";
    std::ofstream outFile(logFile, std::ios::app);
    if (outFile.is_open()) {
        outFile << "Fn_%d_" << __func__ << "\\n";
        outFile.close();
    }
}
#endif  // %s\n''' % (
        TRACE_LOG_MACRO,
        LOG_DIR,
        LOG_FILE_PREFIX,
        function_number,
        TRACE_LOG_MACRO
    )


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
files_to_modify = {}
function_counter = 1
count = 0

print("=" * 80)
print("Function scan and log injection tool")
print("=" * 80)
print("Scan dir:", SCAN_DIR)
print("-" * 80)
print("%-8s %-30s %-45s %-8s %-20s" % ("No.", "Function", "File", "Line", "Author"))
print("-" * 80)

if not os.path.exists(SCAN_DIR):
    print(f"ERROR: scan dir does not exist: {SCAN_DIR}")
    exit(1)

for root, dirs, files in os.walk(SCAN_DIR):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

    for file in files:
        if not file.lower().endswith(SOURCE_EXTENSIONS) or file.lower().endswith(".bak"):
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
                for _, nodes in captures_dict.items():
                    for node in nodes:
                        captures.append(node)

            rel_path = os.path.relpath(path, GIT_REPO_ROOT).replace('\\', '/') if IS_GIT_REPO else os.path.relpath(path, PROJECT_ROOT).replace('\\', '/')

            for node in captures:
                if is_catch_node(node, code):
                    continue

                if is_inside_class_or_struct(node):
                    continue

                if is_probably_macro_function_definition(node):
                    continue

                func_body = find_function_body(node)
                if not func_body:
                    continue

                fname = get_function_name(node)
                if not fname:
                    continue

                line = node.start_point[0] + 1
                author = get_git_blame_author(rel_path, line) if IS_GIT_REPO else None
                insert_byte = func_body.start_byte + 1

                if path not in files_to_modify:
                    files_to_modify[path] = []

                files_to_modify[path].append({
                    "byte_offset": insert_byte,
                    "function_name": fname,
                    "function_number": function_counter
                })

                print("%-8d %-30s %-45s %-8d %-20s" % (function_counter, fname, rel_path, line, author or ""))

                scan_results.append({
                    "function_number": function_counter,
                    "function_name": fname,
                    "file_path": rel_path,
                    "line_number": line,
                    "author": author,
                    "absolute_path": path
                })

                function_counter += 1
                count += 1

        except Exception as e:
            print("Parse failed %s: %s" % (os.path.relpath(path, PROJECT_ROOT), e))
            continue

print("-" * 80)
print("Scan complete, total functions: %d" % count)
print("=" * 80)

if files_to_modify:
    print("\nStart injecting log code...")

    for filepath, functions in files_to_modify.items():
        functions.sort(key=lambda x: x["byte_offset"], reverse=True)

        with open(filepath, "rb") as f:
            content = f.read()

        modified_content = content

        for func_info in functions:
            byte_offset = func_info["byte_offset"]
            func_num = func_info["function_number"]
            log_state, log_start, log_end = find_existing_log_block(modified_content, byte_offset)

            if log_state == "wrapped":
                continue

            if log_state == "bare":
                prefix = ("\n#ifdef %s\n" % TRACE_LOG_MACRO).encode('utf-8')
                suffix = ("\n#endif  // %s\n" % TRACE_LOG_MACRO).encode('utf-8')
                modified_content = (
                    modified_content[:log_start]
                    + prefix
                    + modified_content[log_start:log_end]
                    + suffix
                    + modified_content[log_end:]
                )
                continue

            log_code = build_log_code(func_num).encode('utf-8')
            modified_content = modified_content[:byte_offset] + log_code + modified_content[byte_offset:]

        if modified_content != content:
            with open(filepath, "wb") as f:
                f.write(modified_content)
            print("Modified: %s" % os.path.relpath(filepath, PROJECT_ROOT))
        else:
            print("Skipped existing logs: %s" % os.path.relpath(filepath, PROJECT_ROOT))

    print("Log injection complete.")
else:
    print("No functions found.")

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

csv_file = os.path.join(PROJECT_ROOT, f"function_scan_result_{timestamp}.csv")
with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(["function_number", "function_name", "file_path", "line_number", "author"])
    for result in scan_results:
        writer.writerow([
            result["function_number"],
            result["function_name"],
            result["file_path"],
            result["line_number"],
            result["author"] or ""
        ])
print("CSV result saved to: %s" % csv_file)

json_file = os.path.join(PROJECT_ROOT, f"function_scan_result_{timestamp}.json")
with open(json_file, 'w', encoding='utf-8') as f:
    json.dump({
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_directory": SCAN_DIR,
        "git_repo_root": GIT_REPO_ROOT if IS_GIT_REPO else None,
        "log_directory": LOG_DIR,
        "trace_log_macro": TRACE_LOG_MACRO,
        "total_functions": count,
        "functions": scan_results
    }, f, indent=2, ensure_ascii=False)
print("JSON result saved to: %s" % json_file)
