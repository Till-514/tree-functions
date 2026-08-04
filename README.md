# C++ Function Log Coverage Tools

这组脚本用于扫描 C/C++ 函数、给函数插入运行时日志、去重运行日志，并根据扫描结果和运行日志输出未覆盖函数清单。

## 脚本说明

| 脚本 | 功能 | 是否修改 C++ 源码 |
| --- | --- | --- |
| `scan_functions.py` | 只扫描函数，输出函数清单 CSV 和 JSON | 否 |
| `inject_function_logs.py` | 扫描函数并在每个函数体开头插入日志代码，同时输出函数清单 CSV 和 JSON | 是 |
| `dedup_function_logs.py` | 对运行产生的 `Fn_编号_函数名` 日志去重，每个函数编号只保留一次 | 否 |
| `analyze_uncovered_functions.py` | 根据函数清单 JSON 和日志文件输出未覆盖函数 CSV | 否 |

原来的临时脚本名已整理为更清晰的名字：

| 旧脚本名 | 新脚本名 |
| --- | --- |
| `Tree-sitterV5.5_temp_nologs_havejson.py` | `scan_functions.py` |
| `Tree-sitterV3.5_temp_addlogs.py` | `inject_function_logs.py` |
| `log_dedup.py` | `dedup_function_logs.py` |
| `function_coverage_analyzer.py` | `analyze_uncovered_functions.py` |

## 环境要求

Python 依赖：

```powershell
pip install tree-sitter tree-sitter-cpp
```

脚本默认扫描当前目录下的 `PxImport`：

```python
SCAN_DIR = os.path.join(PROJECT_ROOT, "PxImport")
```

如果要扫描其他目录，修改脚本顶部的 `SCAN_DIR` 即可。

## 推荐流程

### 1. 注入函数日志

```powershell
python inject_function_logs.py
```

该脚本会扫描 `.cpp/.h/.hpp/.c/.cc/.cxx` 文件，过滤 class/struct 内部定义、宏块等容易误判的结构，并在函数体开头插入如下日志：

```cpp
#ifdef PX_EXPORT
{
    std::string logFile = std::string(R"(C:/Users/Public/)") + "FnTrace_" + std::to_string(GetCurrentProcessId()) + ".log";
    std::ofstream outFile(logFile, std::ios::app);
    if (outFile.is_open()) {
        outFile << "Fn_编号_" << __func__ << "\n";
        outFile.close();
    }
}
#endif  // PX_EXPORT
```

日志文件保存到：

```text
C:/Users/Public/FnTrace_<进程ID>.log
```

脚本会避免重复插入：如果函数开头已经存在 `#ifdef PX_EXPORT` 包裹的 `FnTrace_` 日志块，会跳过。

同时生成：

```text
function_scan_result_时间戳.csv
function_scan_result_时间戳.json
```

其中 JSON 是后续覆盖分析的输入。

### 2. 编译并运行程序

确保编译时定义了 `PX_EXPORT`，否则日志代码会被 `#ifdef PX_EXPORT` 屏蔽。

C++ 工程需要能访问这些符号/头文件：

```cpp
#include <string>
#include <fstream>
// GetCurrentProcessId 需要 Windows API 相关头文件
```

运行程序后，会在 `C:/Users/Public/` 下生成 `FnTrace_*.log`。

### 3. 日志去重

如果日志很大，建议先去重：

```powershell
python dedup_function_logs.py -i C:/Users/Public/FnTrace_*.log -o C:/Users/Public/FnTrace_dedup.log
```

也可以输入目录：

```powershell
python dedup_function_logs.py -i C:/Users/Public/ -o C:/Users/Public/FnTrace_dedup.log
```

去重规则：按 `Fn_编号_函数名` 里的编号去重，同一个函数只保留第一次出现的日志行。

### 4. 输出未覆盖函数

```powershell
python analyze_uncovered_functions.py -s function_scan_result_时间戳.json -l C:/Users/Public/FnTrace_dedup.log -o reports
```

输出文件：

```text
reports/uncovered_functions_时间戳.csv
```

CSV 只包含三列：

```text
函数名,函数位置,修改人
```

`函数位置` 格式为：

```text
文件路径:行号
```

## 只扫描不注入

如果只想确认扫描准确性，不修改源码：

```powershell
python scan_functions.py
```

输出：

```text
function_scan_result_时间戳.csv
function_scan_result_时间戳.json
```

## 函数扫描过滤规则

扫描脚本使用 Tree-sitter 解析 C/C++ 的 `function_definition` 节点，并做额外过滤：

- 跳过 `class` / `struct` 定义内部的函数。
- 跳过 `catch` 相关结构。
- 跳过类似 `TEST(...) {}`、`TEST_F(...) {}` 的全大写宏块。
- 避免把返回类型、class 名、struct 名误当成函数名。
- 支持普通函数、类外成员函数、构造/析构函数、operator 函数、尾置返回类型函数。

## 注意事项

- `inject_function_logs.py` 会直接修改源码，建议在 Git 工作区干净时运行，方便检查 diff。
- 函数编号来自扫描顺序。注入日志后，要使用同一次生成的 `function_scan_result_*.json` 去做覆盖分析。
- 如果重新注入或源码发生较大变化，建议重新生成函数清单 JSON。
- `analyze_uncovered_functions.py` 支持直接读取多个日志文件、目录或通配符路径。
