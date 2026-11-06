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

这是音频解码用的 libmpv，不带 mpv 命令行程序。关闭视频解码器、GPU 输出、硬件解码和非必需自动依赖；保留 mpv 必需的 libass／libplacebo 及固定依赖。DSD 经 FFmpeg 解码供音频输出使用，不承诺原生 DSD／DoP 直通。

### 主要构建参数

| 层 | 生效参数／设置 | 用途 |
| --- | --- | --- |
| 通用编译 | `-O2 -fPIC`；Meson `--buildtype=release` | 优化并生成可链接至动态库的代码 |
| 依赖解析 | `--wrap-mode=nodownload -Dauto_features=disabled`；私有 prefix 的 pkgconf `--static` | 不自动下载／探测额外系统媒体库 |
| mpv 产物 | `--default-library=shared -Dlibmpv=true -Dcplayer=false` | 仅交付动态 libmpv，不构建 mpv CLI |
| mpv 功能 | `-Dgpl=false -Dbuild-date=false -Dgl=disabled -Dmanpage-build=disabled` | 禁用 GPL 可选项、构建日期、OpenGL 和 manpage |
| Lua | `-Dlua=lua52`，静态 Lua 5.2.4 | 注册现有客户端初始化选项；不是自动开启脚本 |
| 平台输出 | macOS `-Dcoreaudio=enabled`；Windows `-Dwasapi=enabled -Dwin32-threads=enabled`；Linux `-Dpulse=enabled` | CoreAudio／WASAPI／PulseAudio |
| FFmpeg 链接 | `--enable-static --disable-shared --enable-pic` | FFmpeg 静态链接到 libmpv |
| FFmpeg 范围 | `--disable-autodetect --disable-gpl --disable-nonfree --disable-programs --disable-doc --disable-debug` | 固定依赖与许可范围，不附带 ffmpeg/ffprobe 程序 |
| FFmpeg 媒体功能 | `--disable-avdevice --disable-encoders --disable-muxers` | 只做文件／流的音频解码，不提供采集、编码或封装输出 |
| FFmpeg 加速 | `--disable-hwaccels --disable-videotoolbox --disable-audiotoolbox --disable-x86asm --disable-v4l2-m2m --disable-vaapi --disable-vdpau --disable-vulkan` | 关闭本配方未使用的硬件／视频能力 |
| 裁剪 | FFmpeg 禁用 debug；Windows/Linux 对产物 `--strip-unneeded`；不使用 UPX | 去掉不需要的调试信息，保留正常动态库装载与签名流程 |

FFmpeg 在 `--disable-decoders --disable-demuxers --disable-protocols --disable-filters` 后只启用下列白名单。实际完整命令、Meson options、FFmpeg `config.h/config_components.h/config.mak` 及目标工具链信息保存在对应材料包；运行包不携带它们。

### 音频容器与常见扩展名

下表表示**配方已启用相应容器和解码器**，不等于所有变种都已逐一用真实文件验收。扩展名只是示例，最终取决于文件内容和编码；音频轨可位于多种容器中。

| 常见文件 | 容器 demuxer | 主要已启用解码器／说明 |
| --- | --- | --- |
| `.wav` | `wav` | PCM、ADPCM 系列 |
| `.aif`、`.aiff` | `aiff` | PCM、容器可承载的已启用音频编码 |
| `.flac` | `flac` | `flac` |
| `.mp1`、`.mp2`、`.mp3` | `mp3` | MP1／MP2／MP3，包括对应 float 实现 |
| `.aac`（ADTS） | `aac` | AAC；裸 LOAS/LATM 不在此承诺内 |
| `.m4a`、`.m4b`、`.mp4` 的音频轨 | `mov` | AAC、ALAC 等已启用编码；不提供视频解码 |
| `.ogg`、`.oga`、`.opus` | `ogg` | Vorbis、Opus、FLAC、Speex 等 |
| `.mka`、`.webm`、`.mkv` 的音频轨 | `matroska` | Opus、Vorbis、FLAC、AAC 等；不提供视频解码 |
| `.wma`、`.asf` | `asf` | WMA v1/v2、WMA Pro、Lossless、Voice |
| `.ape` | `ape` | Monkey's Audio `ape` |
| `.wv` | `wv` | WavPack `wavpack` |
| `.tta` | `tta` | True Audio `tta` |
| `.tak` | `tak` | `tak` |
| `.mpc` | `mpc`、`mpc8` | Musepack `mpc7`、`mpc8` |
| `.amr` | `amr` | AMR-NB、AMR-WB |
| `.ac3`、`.eac3`、`.dts` | `ac3`、`eac3`、`dts` | AC-3、E-AC-3、DTS（`dca`） |
| `.dsf` | `dsf` | 原始 DSD，`dsd_lsbf_planar` 等 DSD decoder |
| `.dff`（DSDIFF） | **`iff`** | 原始 DSD 与压缩 **DST**；分别使用 DSD decoder 和 **`dst`** |

完整 demuxer 白名单：

```text
ape, dsf, iff, flac, wav, aiff, mp3, aac, ogg, mov, matroska,
asf, wv, tta, tak, mpc, mpc8, amr, ac3, eac3, dts
```

完整 decoder 请求白名单（`pcm*`、`adpcm*` 为 FFmpeg 通配选择）：

```text
aac, aac_fixed, aac_latm, ac3, alac, als, amrnb, amrwb, ape,
atrac1, atrac3, atrac3al, atrac3p, atrac3pal, cook, dca, dolby_e,
dsd_lsbf, dsd_lsbf_planar, dsd_msbf, dsd_msbf_planar, dst, eac3, flac,
g723_1, g729, gsm, gsm_ms, iac, imc, mace3, mace6, mlp,
mp1, mp1float, mp2, mp2float, mp3, mp3float, mp3adu, mp3adufloat,
mp3on4, mp3on4float, mpc7, mpc8, nellymoser, opus, pcm*, adpcm*,
qcelp, qdm2, qdmc, ralf, real_144, real_288, s302m, shorten, sipr,
speex, tak, truehd, truespeech, tta, twinvq, vorbis, wavpack,
wmalossless, wmapro, wmav1, wmav2, wmavoice
```

有 decoder 不代表对应裸文件 demuxer 已启用。例如 TrueHD／MLP、Shorten、ATRAC、RealAudio 的 decoder 请求不能推导出裸 `.truehd/.mlp/.shn/.oma/.ra` 文件都能打开。未列入容器白名单的 SACD ISO、CAF、HLS/DASH 等不作支持承诺。

### 协议、音频滤镜与使用方设置

- FFmpeg 启用网络支持，但协议仅为 `file,http,tcp,pipe,crypto`。没有启用直接 HTTPS、FTP、UDP 或 HLS/DASH；应用可使用自身受控网关，将已授权上游音频转为本地 HTTP 入口。
- 音频滤镜请求为 `aresample,aformat,anull,atempo,volume,pan,abuffer,abuffersink`，用于格式转换、重采样、倍速、音量及音频图连接。此配方不附带完整滤镜集合，不承诺 EQ／频谱功能。
- Lua 5.2 保留 `load-scripts`、`osc`、`ytdl` 等选项的兼容性。HE-Music 初始化使用 `config=no`、`load-scripts=no`、`osc=no`、`ytdl=no`，关闭配置与脚本自动加载。
- 网络资源入口、播放设备、字幕／歌词界面及权限控制由使用方负责；库的协议白名单不能代替应用权限边界。

### 已验证范围

- 原始 v1 的五目标原生 CI：WAV、DSF、原始 DFF 的加载、进度、暂停、seek、继续、停止，以及 DST 包内原始帧的 decoder 入口，均使用 `ao=null`。v2 库字节与 v1 相同，重组只改变分发范围。
- 本机 macOS arm64：正式运行库和 HE-Music 包内库通过真实 DST 算术压缩 DFF 的只读控制回归；合成 DST 原始帧不能替代这一分支。
- HE-Music 生产网关另外验证了 WAV／DSF／原始 DFF／WebM-Opus 的 `ao=null` race 回归。
- APE 等其余表中格式是配置层面的支持，没有逐种真实样本通过声明；未验证的压缩变种、设备直通或真实声卡效果不外推。

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
