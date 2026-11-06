# mpv-bundles

独立构建、验证和分发面向音乐播放的 libmpv。消费项目下载固定版本的 GitHub Release
资源即可使用，开发和打包过程不再要求编译 mpv／FFmpeg。

固定版本：mpv **0.42.0**、client API **2.6**、FFmpeg **8.0.1**。
所有平台使用同一套源码锁和音频构建配方，包括 **DST 解码器**，用于播放 DST 压缩 DFF。

## 平台

| 下载目标 | 构建环境 | 音频输出 | 兼容基线 |
| --- | --- | --- | --- |
| `darwin/universal` | macOS，合并 amd64／arm64 | CoreAudio | macOS 11 |
| `windows/amd64` | 原生 MSYS2 CLANG64 | WASAPI | Windows amd64 |
| `windows/arm64` | 原生 MSYS2 CLANGARM64 | WASAPI | Windows arm64 |
| `linux/amd64` | 固定 Ubuntu 22.04 容器 | PulseAudio | glibc 2.35 |
| `linux/arm64` | 固定 Ubuntu 22.04 容器 | PulseAudio | glibc 2.35 |

Linux 的基线只描述这些库，不代表消费应用整体的系统要求。各平台是否已正式提供，以
[Releases](https://github.com/he-music/mpv-bundles/releases) 中实际发布的资源为准。

## 下载契约

版本格式为 `v<mpv版本>-<配方修订号>`，例如 `v0.42.0-1`。同版本下的资源包括：

- `manifest.json`：版本、API、配方摘要，以及五个目标的独立 URL、SHA256、大小和库名。
- `libmpv-<系统>-<架构>-<版本>.tar.gz`：目标平台的库、源码、许可证和构建证据。
- `SHA256SUMS`：五个压缩包及顶层清单的 SHA256。

每个压缩包均以 `mpv/` 为根目录。包内 `manifest.json` 记录逐文件大小及 SHA256，
消费方应固定发布版本和可信摘要，校验后再解包。不得把 CI 的临时产物地址当作长期下载源。
发布清单生成时会检查五个资源是否完整、文件摘要是否一致、配方和目标是否匹配。

包内内容：

- `libmpv.2.dylib`、`libmpv-2.dll` 或 `libmpv.so.2`，以及实际需要的 Windows 运行库 DLL。
- `licenses/`、`sources/`、`sources.lock.json`：对应源码归档、下载位置、摘要和许可证。
- `rebuild/`：本次使用的 Python 构建脚本和固定版本构建依赖。
- `build-config/`、`build-info.json`：编译选项、工具链、CI 提交及运行信息。
- `validation.json`：原生空音频输出测试证据；Linux 另有 `linux-audit.json`。

macOS universal 包保留两个架构的构建配置，`validation.json` 标明执行测试时的原生环境。
跨架构编译成功不等于在该架构执行了测试。

## 配方范围

保留常见音频、APE、DSF、原始 DSD DFF 和 DST 压缩 DFF；保留 mpv 必需的
libass／libplacebo 及其固定依赖。关闭视频解码器、GPU 输出和硬件解码等扩展。

编译 Lua 5.2 以保留 `load-scripts`、`osc`、`ytdl` 等客户端选项的兼容性。消费方在
初始化时设置 `config=no`、`load-scripts=no`、`osc=no`、`ytdl=no`，避免自动加载用户脚本。
消费方负责网络资源入口、播放设备及应用自身的权限控制。

## 本机构建

需要 Python 3.10+、C/C++ 编译器和 make；macOS 还需 Xcode 命令行工具。
Windows 必须使用与目标匹配的 MSYS2 CLANG64／CLANGARM64 原生 Python 和工具链，
并通过 MSYS2 安装 Ninja。Linux 在工作流指定的 Ubuntu 22.04 镜像内构建。

```sh
python3 -m venv build/tools
. build/tools/bin/activate
python -m pip install -r requirements-build.txt
python scripts/mpv/prepare.py --target darwin/universal
python scripts/mpv/prepare.py --check --offline --target darwin/universal
python scripts/release.py package --target darwin/universal --version v0.42.0-1
```

`--fetch-only` 只下载并校验源码；`--offline` 禁止下载缺失源码；`--force` 强制重建。
构建缓存位于 `build/mpv-cache/`，发布资源位于 `dist/`，二进制不进入 Git。

## 验证

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
python scripts/mpv/smoke.py --resources build/mpv-cache/prepared/darwin-universal/mpv
```

烟雾测试使用 `ao=null`，验证 client API、生产初始化选项，以及合成 WAV／DSF／原始
DFF 的加载、进度、暂停、跳转、继续、停止和实例销毁。它不证明真实声卡的听感或设备切换。

额外验证本地 DST 压缩 DFF：

```sh
python scripts/mpv/smoke.py \
  --resources build/mpv-cache/prepared/darwin-universal/mpv \
  --dst-sample /absolute/path/to/compressed.dff \
  --output build/dst-validation.json
```

该参数要求解码器确实为 `dst`，只读样本且不将文件名或音频写入发布包。未提供样本时，
报告会明确标记 DST 样本回归未执行；构建仍强制检查 FFmpeg 的 `CONFIG_DST_DECODER=1`。

## 发布

推送版本标签后，Actions 在五个原生构建目标验证和打包。全部成功才创建草稿 Release，
附带五个独立压缩包、顶层清单和校验和。正式发布前应核对各平台的运行证据、动态依赖、
Windows 工具链运行库来源与许可证。配方修改必须提升修订号，保持旧版资源不可变。

第三方代码的具体许可及重新分发材料见 [LICENSES.md](LICENSES.md)。
