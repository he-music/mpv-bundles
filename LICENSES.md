# libmpv 发布材料与许可证

每个目标压缩包包含该次构建使用的源码归档、SHA256、构建脚本、配置和上游许可证。
libmpv 以可独立替换的动态库交付，使用方需遵守相应 LGPL 的修改、重新链接和源码提供要求。

本仓库构建／验证脚本由 HE-Music 的工具迁移而来，沿用根目录 [LICENSE](LICENSE) 中的
AGPL-3.0。以下第三方库保留各自上游许可证；脚本许可不改变独立库的原有许可。

| 组件 | 本配方使用的许可或说明 |
| --- | --- |
| mpv | LGPL；配置 `gpl=false`，保留该版本的 LGPL 及版权声明 |
| FFmpeg | LGPL；关闭 GPL／nonfree 组件，包含该版本许可文件 |
| libplacebo、FriBidi | LGPL；保留各自的上游声明 |
| FreeType | 本配方选择 FTL，附带 `FTL.TXT` 与上游许可说明 |
| libass、HarfBuzz、fast_float、Vulkan-Headers、pkgconf | 各自的上游许可，随源码及 `licenses/` 提供 |
| Lua 5.2.4 | MIT；静态链接以保留客户端选项，附带 `doc/readme.html` 中的版权和许可 |
| libiconv／libcharset | 库与头文件为 LGPL-2.1-or-later；工具和文档保持各自 GPL 声明，详见随包 README 与许可 |

FreeType 声明：Portions of this software are copyright © 2025 The FreeType Project
(https://freetype.org). All rights reserved.

`load-scripts=no` 由消费方初始化时设置，Lua 本身仍包含在库内。
`rebuild/` 保存确切的构建脚本；`build-config/` 保存编译选项和补丁结果摘要，源码归档保持上游原样。

Windows 包固定原生 MSYS2 工具链／运行库的版本、许可证摘要和实际随包 DLL 摘要。最终 LLD
map、实际使用的 archive／object 摘要及包归属保存在 `build-config/`；核对记录位于
`windows-runtime-review.json`。缺失或变化的材料会阻止发行，仍为
`pending-toolchain-runtime-review` 的产物不能进入发行清单。

`sources/windows/` 提供 libiconv 1.19.1 的完整上游源码、固定 MinGW 源树及匹配的 MSYS2
配方／补丁快照，保守覆盖可能使用的 LGPL headers/runtime。LLVM 部分保留实际
Apache-2.0 WITH LLVM-exception 和历史 notices，并记录包版本及固定的上游源码／补丁配方；
该配方快照不表示已经内含 LLVM 完整上游源码。上述记录不声称静态或 LGPL 代码不存在。
Windows 系统 DLL 由目标操作系统提供，不随包复制。

Linux 的 glibc、libstdc++ 和 PulseAudio 为系统依赖，包内记录实际依赖、符号版本、构建系统包和
固定容器镜像。它们不作为私有运行库嵌入包中。macOS 的 Apple 系统库与框架同样由系统提供。

私有音频仅用于本地只读回归，不进入源码仓库、测试素材或 Release。
