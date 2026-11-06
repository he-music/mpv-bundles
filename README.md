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

版本格式为 `v<mpv版本>-<产物修订号>`，例如 `v0.42.0-2`。从修订 2 起，运行库和材料拆分交付：

- `libmpv-<系统>-<架构>-<版本>.tar.gz`：仅原生运行库、必要动态依赖、许可证与包内校验清单，根目录 `mpv/`。
- `libmpv-materials-<版本>.tar.gz`：一份共享材料包，根目录 `materials/`。源码归档和相同重建脚本去重，各目标保存配置、工具链和链接证据；省略冗长日志。
- `manifest.json`：schema 2，固定五份运行归档和材料归档的 URL、大小及摘要。每目标的 `runtimeFiles` 和 `licenseFiles` 直接固定安装文件的字节摘要，支持安装目录不含清单时离线校验。
- `SHA256SUMS`：六份归档与顶层清单共七项，不自引用。

消费方只下载目标运行归档。应用的 `mpv` 目录只安装 `runtimeFiles` 中的动态库；`licenseFiles` 归入应用统一的第三方许可目录。包内 manifest、源码、构建配置和重建脚本不复制进应用。

`licenses/SOURCE-MATERIALS.json` 给出同版本材料包的固定 URL、SHA256、大小和来源，必要许可与对应源码关系保留。材料包的 manifest 记录逐文件摘要，以及各目标原始清单、源码和重建入口的映射。原始源码归档字节保持不变；不携带整个系统 SDK、编译器、对象缓存或私有音频。

v0.42.0-1 是历史完整归档，运行库及材料一起保存。可用固定的来源清单 SHA256，将已经通过五平台原生测试的完整产物重组为精简版本，所有运行库字节保持不变。新版本记录原核心配方摘要与来源，打包方法另行保存，不把重组当成重新编译。

macOS universal 的两个架构配置与原生验证证据保留在材料包；跨架构编译成功不等于在该架构执行了测试。

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

CI 从本仓库的固定源码快照 `sources-v0.42.0-r2` 预填缓存，再以离线模式编译；快照内的
11 个核心库归档与源码锁逐一对应，另有 Windows 运行库的 3 个固定源码／配方归档。
构建重新校验 SHA256，避免各原始下载站的瞬时断连中止整个平台构建。快照不含播放器二进制。

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

CI 另运行 `scripts/validate_dst.py`，自动生成 DST 容器中的原始帧并实际调用 `dst`
解码器，验证播放控制，将证据写入 `dst-frame-validation.json`。这一用例不覆盖 DST 的算术
压缩分支；报告明确记录该范围，也不需要上传私有音频。

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

推送版本标签，或手动启动原生构建工作流并勾选 `create_release`，会在五目标验证和完整中间归档生成后，将其拆为运行包及共享材料包，再创建草稿 Release。

仅调整打包范围时可启动 `Repackage verified libmpv bundles`：提供固定完整来源版本、来源 manifest SHA256 和新的产物修订号。工作流下载并核验所有输入，运行拆分测试并创建八资源草稿；不执行归档中的二进制，也不把重组报告为新的平台编译或运行测试。正式发布前应回读校验全部归档、材料映射和二进制字节。

Windows 运行库由 `windows-runtimes.lock.json` 固定实际 DLL、包版本和许可证摘要，保存最终
LLD map 及实际链接 archive／object 的摘要和包归属。包内提供 libiconv 完整源码、完整
MinGW 源树和匹配的 MSYS2 配方与补丁；保守覆盖可能使用的 LGPL headers/runtime。
材料或输入校验不完整会阻止生成发行清单。配方修改必须提升修订号，保持已发布资源不可变。

第三方代码的具体许可及重新分发材料见 [LICENSES.md](LICENSES.md)。
